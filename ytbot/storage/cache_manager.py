"""
Cache manager for YTBot

Manages local cache files when Nextcloud is unavailable.
Provides persistent queue for retry uploads.
"""

import json
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List

from ..core.config import CONFIG
from ..core.logger import get_logger

logger = get_logger(__name__)


class CacheManager:
    """
    Thread-safe cache file manager with persistent queue

    Manages files that failed to upload to Nextcloud, storing metadata
    in a JSON file for later retry processing.
    """

    def __init__(self, cache_dir: Optional[str] = None):
        """
        Initialize cache manager

        Args:
            cache_dir: Directory for cache files and queue JSON.
                      Defaults to local storage path if not specified.
        """
        # Use local storage path as default cache directory
        if cache_dir is None:
            cache_dir = CONFIG['local_storage']['path']

        self.cache_dir = Path(cache_dir)
        self.queue_file = self.cache_dir / "cache_queue.json"

        # Thread lock for safe concurrent access
        self._lock = threading.Lock()

        # Ensure cache directory exists
        self._ensure_cache_directory()

        # Load existing cache queue
        self._cache_queue: List[Dict[str, Any]] = self._load_queue()

        logger.info(f"CacheManager initialized with {len(self._cache_queue)} cached items")

    def _ensure_cache_directory(self):
        """Ensure cache directory exists"""
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            logger.debug(f"Cache directory ensured: {self.cache_dir}")
        except Exception as e:
            logger.error(f"Failed to create cache directory: {e}")
            raise

    def _load_queue(self) -> List[Dict[str, Any]]:
        """
        Load cache queue from JSON file

        Returns:
            List of cached file entries
        """
        try:
            if self.queue_file.exists():
                with open(self.queue_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    queue = data.get('cache_queue', [])
                    logger.debug(f"Loaded {len(queue)} items from cache queue")
                    return queue
            else:
                logger.debug("No existing cache queue file found, starting fresh")
                return []
        except Exception as e:
            logger.error(f"Failed to load cache queue: {e}")
            return []

    def _save_queue(self) -> bool:
        """
        Save cache queue to JSON file (thread-safe)

        Returns:
            bool: True if save successful, False otherwise
        """
        try:
            data = {
                "cache_queue": self._cache_queue,
                "last_updated": datetime.now().isoformat(),
                "total_items": len(self._cache_queue)
            }

            # Write to temporary file first, then rename for atomicity
            temp_file = self.queue_file.with_suffix('.tmp')
            with open(temp_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

            # Atomic rename
            temp_file.rename(self.queue_file)

            logger.debug(f"Cache queue saved: {len(self._cache_queue)} items")
            return True
        except Exception as e:
            logger.error(f"Failed to save cache queue: {e}")
            return False

    def add_to_cache(
        self,
        file_path: str,
        filename: str,
        content_type: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        Add a file to the cache queue

        Args:
            file_path: Absolute path to the cached file
            filename: Original filename
            content_type: Type of content (video, audio, etc.)
            metadata: Optional metadata dictionary

        Returns:
            bool: True if added successfully, False otherwise
        """
        with self._lock:
            try:
                # Verify file exists
                if not os.path.exists(file_path):
                    logger.error(f"Cannot add to cache: file does not exist: {file_path}")
                    return False

                # Create cache entry
                entry = {
                    "file_path": file_path,
                    "filename": filename,
                    "content_type": content_type,
                    "timestamp": datetime.now().isoformat(),
                    "metadata": metadata or {}
                }

                # Add to queue
                self._cache_queue.append(entry)

                # Persist to disk
                if self._save_queue():
                    logger.info(f"File added to cache: {filename} ({content_type})")
                    logger.debug(f"Cache entry: {entry}")
                    return True
                else:
                    # Remove from memory if save failed
                    self._cache_queue.pop()
                    return False

            except Exception as e:
                logger.error(f"Failed to add file to cache: {e}")
                return False

    def get_cache_queue(self) -> List[Dict[str, Any]]:
        """
        Get all cached file entries (copy of queue)

        Returns:
            List of cache entries sorted by timestamp (oldest first)
        """
        with self._lock:
            # Return a copy sorted by timestamp
            return sorted(
                self._cache_queue.copy(),
                key=lambda x: x.get('timestamp', '')
            )

    def get_next_cache_item(self) -> Optional[Dict[str, Any]]:
        """
        Get the next cache item to process (oldest first)

        Returns:
            Cache entry dict or None if queue is empty
        """
        with self._lock:
            if not self._cache_queue:
                return None

            # Sort by timestamp and return oldest
            sorted_queue = sorted(
                self._cache_queue,
                key=lambda x: x.get('timestamp', '')
            )
            return sorted_queue[0].copy() if sorted_queue else None

    def get_cache_item_by_path(self, file_path: str) -> Optional[Dict[str, Any]]:
        """
        Get a specific cache item by file path

        Args:
            file_path: File path to search for

        Returns:
            Cache entry dict or None if not found
        """
        with self._lock:
            for entry in self._cache_queue:
                if entry.get('file_path') == file_path:
                    return entry.copy()
            return None

    def remove_from_cache(self, file_path: str) -> bool:
        """
        Remove a file from the cache queue

        Args:
            file_path: File path to remove

        Returns:
            bool: True if removed successfully, False otherwise
        """
        with self._lock:
            try:
                # Find and remove the entry
                original_length = len(self._cache_queue)
                self._cache_queue = [
                    entry for entry in self._cache_queue
                    if entry.get('file_path') != file_path
                ]

                if len(self._cache_queue) < original_length:
                    # Persist changes
                    if self._save_queue():
                        logger.info(f"File removed from cache: {file_path}")
                        return True
                    else:
                        logger.error(f"Failed to persist cache after removal: {file_path}")
                        return False
                else:
                    logger.warning(f"File not found in cache: {file_path}")
                    return False

            except Exception as e:
                logger.error(f"Failed to remove file from cache: {e}")
                return False

    def delete_cached_file(self, file_path: str) -> bool:
        """
        Delete a cached file from disk and remove from queue

        Args:
            file_path: File path to delete

        Returns:
            bool: True if deleted successfully, False otherwise
        """
        with self._lock:
            try:
                # Remove from queue first
                removed = False
                original_length = len(self._cache_queue)
                self._cache_queue = [
                    entry for entry in self._cache_queue
                    if entry.get('file_path') != file_path
                ]

                if len(self._cache_queue) < original_length:
                    removed = True

                # Delete file from disk
                if os.path.exists(file_path):
                    os.remove(file_path)
                    logger.info(f"Cache file deleted from disk: {file_path}")

                # Persist queue changes
                if removed:
                    self._save_queue()

                return True

            except Exception as e:
                logger.error(f"Failed to delete cached file: {e}")
                return False

    def clear_cache(self) -> Dict[str, Any]:
        """
        Clear all cached files from disk and queue

        Returns:
            dict: Statistics about cleared files
        """
        with self._lock:
            stats = {
                "files_deleted": 0,
                "files_not_found": 0,
                "errors": [],
                "space_freed_bytes": 0
            }

            for entry in self._cache_queue:
                file_path = entry.get('file_path')
                if not file_path:
                    continue

                try:
                    if os.path.exists(file_path):
                        file_size = os.path.getsize(file_path)
                        os.remove(file_path)
                        stats["files_deleted"] += 1
                        stats["space_freed_bytes"] += file_size
                        logger.debug(f"Deleted cached file: {file_path}")
                    else:
                        stats["files_not_found"] += 1
                        logger.debug(f"Cache file not found: {file_path}")
                except Exception as e:
                    stats["errors"].append(f"{file_path}: {str(e)}")
                    logger.error(f"Failed to delete cache file {file_path}: {e}")

            # Clear queue
            self._cache_queue = []
            self._save_queue()

            logger.info(
                f"Cache cleared: {stats['files_deleted']} files deleted, "
                f"{stats['files_not_found']} not found, "
                f"{stats['space_freed_bytes'] / 1024 / 1024:.2f}MB freed"
            )

            return stats

    def get_cache_stats(self) -> Dict[str, Any]:
        """
        Get statistics about the cache

        Returns:
            dict: Cache statistics
        """
        with self._lock:
            total_size = 0
            files_exist = 0
            files_missing = 0
            content_types = {}

            for entry in self._cache_queue:
                file_path = entry.get('file_path')
                content_type = entry.get('content_type', 'unknown')

                # Count content types
                content_types[content_type] = content_types.get(content_type, 0) + 1

                # Check file existence and size
                if file_path and os.path.exists(file_path):
                    files_exist += 1
                    try:
                        total_size += os.path.getsize(file_path)
                    except Exception:
                        pass
                else:
                    files_missing += 1

            return {
                "total_items": len(self._cache_queue),
                "files_exist": files_exist,
                "files_missing": files_missing,
                "total_size_bytes": total_size,
                "total_size_mb": total_size / (1024 * 1024),
                "content_types": content_types,
                "cache_dir": str(self.cache_dir),
                "queue_file": str(self.queue_file)
            }

    def cleanup_missing_files(self) -> int:
        """
        Remove entries for files that no longer exist on disk

        Returns:
            int: Number of entries removed
        """
        with self._lock:
            original_length = len(self._cache_queue)

            self._cache_queue = [
                entry for entry in self._cache_queue
                if entry.get('file_path') and os.path.exists(entry['file_path'])
            ]

            removed_count = original_length - len(self._cache_queue)

            if removed_count > 0:
                self._save_queue()
                logger.info(f"Cleaned up {removed_count} missing file entries from cache")

            return removed_count

    def get_oldest_items(self, count: int = 10) -> List[Dict[str, Any]]:
        """
        Get the oldest N items in the cache queue

        Args:
            count: Number of items to return

        Returns:
            List of oldest cache entries
        """
        with self._lock:
            sorted_queue = sorted(
                self._cache_queue.copy(),
                key=lambda x: x.get('timestamp', '')
            )
            return sorted_queue[:count]

    def get_items_by_content_type(self, content_type: str) -> List[Dict[str, Any]]:
        """
        Get all cache items of a specific content type

        Args:
            content_type: Type of content to filter by

        Returns:
            List of matching cache entries
        """
        with self._lock:
            return [
                entry.copy() for entry in self._cache_queue
                if entry.get('content_type') == content_type
            ]


# Global instance
cache_manager = CacheManager()


def get_cache_manager() -> CacheManager:
    """Get the global cache manager instance"""
    return cache_manager
