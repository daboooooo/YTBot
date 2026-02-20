"""
User state management for YTBot

Manages user interaction states with timeout cleanup and thread-safe operations.
"""

import json
import threading
import time
from enum import Enum
from typing import Dict, Any, Optional
from pathlib import Path

from .logger import get_logger

logger = get_logger(__name__)


class UserState(Enum):
    """User interaction states"""
    IDLE = "idle"  # 空闲状态
    WAITING_DOWNLOAD_TYPE = "waiting_download_type"  # 等待用户选择下载类型
    WAITING_CONFIRMATION = "waiting_confirmation"  # 等待用户确认
    DOWNLOADING = "downloading"  # 下载中
    ERROR = "error"  # 错误状态


class UserStateManager:
    """
    Manages user interaction states with automatic timeout cleanup.

    Thread-safe implementation for managing multi-step user interactions.
    Supports optional state persistence to disk.
    """

    def __init__(
        self,
        timeout: int = 300,
        persistence_file: Optional[str] = None,
        cleanup_interval: int = 60
    ):
        """
        Initialize UserStateManager.

        Args:
            timeout: State timeout in seconds (default: 300)
            persistence_file: Optional file path for state persistence
            cleanup_interval: Interval for cleanup thread in seconds (default: 60)
        """
        self.timeout = timeout
        self.persistence_file = persistence_file
        self.cleanup_interval = cleanup_interval

        # Thread-safe state storage
        # Structure: {user_id: {"state": UserState, "data": dict, "timestamp": float}}
        self._states: Dict[int, Dict[str, Any]] = {}
        self._lock = threading.RLock()

        # Cleanup thread control
        self._cleanup_thread: Optional[threading.Thread] = None
        self._stop_cleanup = threading.Event()

        # Load persisted states if available
        if self.persistence_file:
            self._load_states()

        # Start cleanup thread
        self._start_cleanup_thread()

        logger.info(
            f"UserStateManager initialized with timeout={timeout}s, "
            f"persistence={persistence_file}, cleanup_interval={cleanup_interval}s"
        )

    def set_state(
        self,
        user_id: int,
        state: UserState,
        data: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        Set user state with optional data.

        Args:
            user_id: Telegram user/chat ID
            state: New state to set
            data: Optional data associated with the state
        """
        with self._lock:
            self._states[user_id] = {
                "state": state,
                "data": data or {},
                "timestamp": time.time()
            }

            # Persist if enabled
            if self.persistence_file:
                self._save_states()

            logger.debug(f"User {user_id} state set to {state.value}")

    def get_state(self, user_id: int) -> Optional[Dict[str, Any]]:
        """
        Get user state information.

        Args:
            user_id: Telegram user/chat ID

        Returns:
            State dict with 'state', 'data', 'timestamp' keys, or None if not found
        """
        with self._lock:
            state_info = self._states.get(user_id)

            if state_info is None:
                return None

            # Check if state has expired
            if self._is_expired(state_info["timestamp"]):
                self.clear_state(user_id)
                return None

            return state_info.copy()

    def get_user_state_enum(self, user_id: int) -> UserState:
        """
        Get user state as enum value.

        Args:
            user_id: Telegram user/chat ID

        Returns:
            UserState enum (defaults to IDLE if not found or expired)
        """
        state_info = self.get_state(user_id)
        if state_info is None:
            return UserState.IDLE
        return state_info["state"]

    def get_state_data(self, user_id: int) -> Optional[Dict[str, Any]]:
        """
        Get data associated with user state.

        Args:
            user_id: Telegram user/chat ID

        Returns:
            State data dict or None if not found
        """
        state_info = self.get_state(user_id)
        if state_info is None:
            return None
        return state_info.get("data")

    def update_state_data(
        self,
        user_id: int,
        data: Dict[str, Any],
        merge: bool = True
    ) -> bool:
        """
        Update data for existing user state.

        Args:
            user_id: Telegram user/chat ID
            data: Data to update
            merge: If True, merge with existing data; if False, replace

        Returns:
            True if updated successfully, False if state doesn't exist
        """
        with self._lock:
            state_info = self._states.get(user_id)

            if state_info is None or self._is_expired(state_info["timestamp"]):
                return False

            if merge:
                state_info["data"].update(data)
            else:
                state_info["data"] = data

            state_info["timestamp"] = time.time()

            # Persist if enabled
            if self.persistence_file:
                self._save_states()

            logger.debug(f"User {user_id} state data updated")
            return True

    def clear_state(self, user_id: int) -> bool:
        """
        Clear user state.

        Args:
            user_id: Telegram user/chat ID

        Returns:
            True if state was cleared, False if state didn't exist
        """
        with self._lock:
            if user_id in self._states:
                del self._states[user_id]

                # Persist if enabled
                if self.persistence_file:
                    self._save_states()

                logger.debug(f"User {user_id} state cleared")
                return True
            return False

    def has_state(self, user_id: int) -> bool:
        """
        Check if user has an active (non-expired) state.

        Args:
            user_id: Telegram user/chat ID

        Returns:
            True if user has active state
        """
        return self.get_state(user_id) is not None

    def is_in_state(self, user_id: int, state: UserState) -> bool:
        """
        Check if user is in a specific state.

        Args:
            user_id: Telegram user/chat ID
            state: State to check

        Returns:
            True if user is in the specified state
        """
        current_state = self.get_user_state_enum(user_id)
        return current_state == state

    def get_all_active_users(self) -> Dict[int, Dict[str, Any]]:
        """
        Get all users with active states.

        Returns:
            Dict of user_id to state info
        """
        with self._lock:
            active_users = {}

            for user_id, state_info in self._states.items():
                if not self._is_expired(state_info["timestamp"]):
                    active_users[user_id] = state_info.copy()

            return active_users

    def get_users_in_state(self, state: UserState) -> Dict[int, Dict[str, Any]]:
        """
        Get all users in a specific state.

        Args:
            state: State to filter by

        Returns:
            Dict of user_id to state info
        """
        with self._lock:
            users_in_state = {}

            for user_id, state_info in self._states.items():
                if state_info["state"] == state and not self._is_expired(state_info["timestamp"]):
                    users_in_state[user_id] = state_info.copy()

            return users_in_state

    def cleanup_expired_states(self) -> int:
        """
        Remove all expired states.

        Returns:
            Number of states removed
        """
        with self._lock:
            expired_users = []

            for user_id, state_info in self._states.items():
                if self._is_expired(state_info["timestamp"]):
                    expired_users.append(user_id)

            for user_id in expired_users:
                del self._states[user_id]
                logger.info(f"User {user_id} state expired and removed")

            if expired_users and self.persistence_file:
                self._save_states()

            return len(expired_users)

    def clear_all_states(self) -> int:
        """
        Clear all user states.

        Returns:
            Number of states cleared
        """
        with self._lock:
            count = len(self._states)
            self._states.clear()

            if self.persistence_file:
                self._save_states()

            logger.info(f"All {count} user states cleared")
            return count

    def get_state_age(self, user_id: int) -> Optional[float]:
        """
        Get age of user state in seconds.

        Args:
            user_id: Telegram user/chat ID

        Returns:
            Age in seconds or None if state doesn't exist
        """
        state_info = self.get_state(user_id)
        if state_info is None:
            return None

        return time.time() - state_info["timestamp"]

    def get_state_info_summary(self, user_id: int) -> str:
        """
        Get human-readable state summary for a user.

        Args:
            user_id: Telegram user/chat ID

        Returns:
            State summary string
        """
        state_info = self.get_state(user_id)
        if state_info is None:
            return f"User {user_id}: No active state"

        state = state_info["state"]
        age = time.time() - state_info["timestamp"]
        data_keys = list(state_info["data"].keys())

        summary = (
            f"User {user_id}: State={state.value}, "
            f"Age={age:.1f}s, "
            f"Data keys={data_keys}"
        )
        return summary

    def _is_expired(self, timestamp: float) -> bool:
        """Check if a timestamp is expired based on timeout."""
        return (time.time() - timestamp) > self.timeout

    def _start_cleanup_thread(self) -> None:
        """Start the background cleanup thread."""
        if self._cleanup_thread is None or not self._cleanup_thread.is_alive():
            self._stop_cleanup.clear()
            self._cleanup_thread = threading.Thread(
                target=self._cleanup_loop,
                daemon=True,
                name="UserStateCleanup"
            )
            self._cleanup_thread.start()
            logger.info("User state cleanup thread started")

    def _cleanup_loop(self) -> None:
        """Background cleanup loop."""
        while not self._stop_cleanup.is_set():
            try:
                # Sleep for cleanup interval
                self._stop_cleanup.wait(self.cleanup_interval)

                if self._stop_cleanup.is_set():
                    break

                # Perform cleanup
                removed_count = self.cleanup_expired_states()
                if removed_count > 0:
                    logger.info(f"Cleaned up {removed_count} expired user states")

            except Exception as e:
                logger.error(f"Error in cleanup loop: {e}")

    def _save_states(self) -> bool:
        """
        Save states to persistence file.

        Returns:
            True if saved successfully
        """
        if not self.persistence_file:
            return False

        try:
            # Prepare data for serialization
            save_data = {}
            with self._lock:
                for user_id, state_info in self._states.items():
                    save_data[str(user_id)] = {
                        "state": state_info["state"].value,
                        "data": state_info["data"],
                        "timestamp": state_info["timestamp"]
                    }

            # Write to file
            file_path = Path(self.persistence_file)
            file_path.parent.mkdir(parents=True, exist_ok=True)

            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(save_data, f, indent=2, ensure_ascii=False)

            logger.debug(f"Saved {len(save_data)} user states to {self.persistence_file}")
            return True

        except Exception as e:
            logger.error(f"Failed to save states: {e}")
            return False

    def _load_states(self) -> bool:
        """
        Load states from persistence file.

        Returns:
            True if loaded successfully
        """
        if not self.persistence_file:
            return False

        try:
            file_path = Path(self.persistence_file)

            if not file_path.exists():
                logger.debug(f"No persistence file found at {self.persistence_file}")
                return False

            with open(file_path, 'r', encoding='utf-8') as f:
                save_data = json.load(f)

            # Restore states
            loaded_count = 0

            with self._lock:
                for user_id_str, state_info in save_data.items():
                    user_id = int(user_id_str)

                    # Skip expired states
                    if self._is_expired(state_info["timestamp"]):
                        continue

                    # Convert state string back to enum
                    try:
                        state = UserState(state_info["state"])
                    except ValueError:
                        logger.warning(f"Unknown state '{state_info['state']}' for user {user_id}")
                        continue

                    self._states[user_id] = {
                        "state": state,
                        "data": state_info["data"],
                        "timestamp": state_info["timestamp"]
                    }
                    loaded_count += 1

            logger.info(f"Loaded {loaded_count} user states from {self.persistence_file}")
            return True

        except Exception as e:
            logger.error(f"Failed to load states: {e}")
            return False

    def shutdown(self) -> None:
        """
        Shutdown the state manager.

        Stops cleanup thread and optionally saves states.
        """
        logger.info("Shutting down UserStateManager")

        # Stop cleanup thread
        self._stop_cleanup.set()
        if self._cleanup_thread and self._cleanup_thread.is_alive():
            self._cleanup_thread.join(timeout=5)

        # Final save
        if self.persistence_file:
            self._save_states()

        logger.info("UserStateManager shutdown complete")

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.shutdown()
        return False

    def __len__(self) -> int:
        """Get number of active states."""
        return len(self.get_all_active_users())

    def __contains__(self, user_id: int) -> bool:
        """Check if user has active state."""
        return self.has_state(user_id)

    def __repr__(self) -> str:
        """String representation."""
        active_count = len(self.get_all_active_users())
        return (
            f"UserStateManager(timeout={self.timeout}, "
            f"active_users={active_count}, "
            f"persistence={self.persistence_file is not None})"
        )
