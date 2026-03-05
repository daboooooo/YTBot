"""
Process lock mechanism to prevent multiple bot instances from running simultaneously.

Uses file-based locking with PID tracking for cross-platform compatibility.
"""

import os
import sys
import atexit
import signal
from pathlib import Path
from typing import Optional
from filelock import FileLock, Timeout

from .logger import get_logger

logger = get_logger(__name__)


class ProcessLock:
    """
    Process-level lock to ensure only one bot instance runs at a time.
    
    Features:
    - File-based locking using filelock library
    - PID tracking to detect stale locks
    - Automatic cleanup on exit
    - Cross-platform support
    """
    
    _instance: Optional['ProcessLock'] = None
    _lock_file: Optional[Path] = None
    _file_lock: Optional[FileLock] = None
    _pid_file: Optional[Path] = None
    _acquired: bool = False
    
    def __new__(cls, lock_dir: Optional[str] = None):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialize(lock_dir)
        return cls._instance
    
    def _initialize(self, lock_dir: Optional[str] = None):
        """Initialize lock file paths."""
        if lock_dir is None:
            lock_dir = os.path.expanduser("~/.ytbot")
        
        lock_path = Path(lock_dir)
        lock_path.mkdir(parents=True, exist_ok=True)
        
        self._lock_file = lock_path / "ytbot.lock"
        self._pid_file = lock_path / "ytbot.pid"
        self._file_lock = FileLock(str(self._lock_file))
    
    def acquire(self, timeout: float = 0) -> bool:
        """
        Acquire the process lock.
        
        Args:
            timeout: Maximum time to wait for lock (0 = non-blocking)
            
        Returns:
            True if lock acquired, False otherwise
        """
        if self._acquired:
            logger.debug("Process lock already held by this instance")
            return True
        
        try:
            # Try to acquire the lock
            self._file_lock.acquire(timeout=timeout)
            
            # Check for stale lock (another process died without releasing)
            if self._is_stale_lock():
                logger.warning("Detected stale lock from previous process, cleaning up")
                self._cleanup_stale_lock()
            
            # Write current PID
            self._write_pid()
            
            # Register cleanup on exit
            atexit.register(self.release)
            
            self._acquired = True
            logger.info("✅ Process lock acquired - single instance mode active")
            return True
            
        except Timeout:
            # Lock is held by another process
            pid = self._read_pid()
            if pid:
                logger.error(f"❌ Another YTBot instance is already running (PID: {pid})")
                logger.error("   Use 'ytbot --stop' to stop the existing instance")
                logger.error("   Or manually kill the process if it's stuck")
            else:
                logger.error("❌ Another YTBot instance is already running")
            return False
            
        except Exception as e:
            logger.error(f"❌ Failed to acquire process lock: {e}")
            return False
    
    def release(self):
        """Release the process lock."""
        if not self._acquired:
            return
        
        try:
            # Remove PID file
            if self._pid_file and self._pid_file.exists():
                self._pid_file.unlink()
            
            # Release file lock
            if self._file_lock:
                self._file_lock.release()
            
            self._acquired = False
            logger.info("🔓 Process lock released")
            
        except Exception as e:
            logger.warning(f"⚠️ Error releasing process lock: {e}")
    
    def _is_stale_lock(self) -> bool:
        """Check if the lock is stale (process no longer exists)."""
        pid = self._read_pid()
        if pid is None:
            return True
        
        try:
            # Check if process exists
            if sys.platform == 'win32':
                import ctypes
                kernel32 = ctypes.windll.kernel32
                handle = kernel32.OpenProcess(1, False, pid)
                if handle:
                    kernel32.CloseHandle(handle)
                    return False
                return True
            else:
                # Unix-like systems
                os.kill(pid, 0)
                return False
        except (OSError, ProcessLookupError):
            return True
    
    def _cleanup_stale_lock(self):
        """Clean up stale lock files."""
        try:
            if self._pid_file and self._pid_file.exists():
                self._pid_file.unlink()
        except Exception as e:
            logger.warning(f"⚠️ Error cleaning up stale PID file: {e}")
    
    def _read_pid(self) -> Optional[int]:
        """Read PID from PID file."""
        try:
            if self._pid_file and self._pid_file.exists():
                content = self._pid_file.read_text().strip()
                return int(content)
        except (ValueError, IOError):
            pass
        return None
    
    def _write_pid(self):
        """Write current PID to PID file."""
        try:
            if self._pid_file:
                self._pid_file.write_text(str(os.getpid()))
        except Exception as e:
            logger.warning(f"⚠️ Error writing PID file: {e}")
    
    @property
    def is_acquired(self) -> bool:
        """Check if lock is currently held."""
        return self._acquired
    
    @classmethod
    def get_running_pid(cls) -> Optional[int]:
        """Get PID of running instance if any."""
        lock_dir = Path(os.path.expanduser("~/.ytbot"))
        pid_file = lock_dir / "ytbot.pid"
        
        try:
            if pid_file.exists():
                content = pid_file.read_text().strip()
                pid = int(content)
                
                # Verify process still exists
                try:
                    if sys.platform == 'win32':
                        import ctypes
                        kernel32 = ctypes.windll.kernel32
                        handle = kernel32.OpenProcess(1, False, pid)
                        if handle:
                            kernel32.CloseHandle(handle)
                            return pid
                    else:
                        os.kill(pid, 0)
                        return pid
                except (OSError, ProcessLookupError):
                    pass
        except (ValueError, IOError):
            pass
        
        return None
    
    @classmethod
    def force_release(cls):
        """Force release lock (use with caution)."""
        lock_dir = Path(os.path.expanduser("~/.ytbot"))
        lock_file = lock_dir / "ytbot.lock"
        pid_file = lock_dir / "ytbot.pid"
        
        try:
            if pid_file.exists():
                pid_file.unlink()
            if lock_file.exists():
                lock_file.unlink()
            logger.info("🔓 Process lock forcefully released")
        except Exception as e:
            logger.error(f"❌ Error force releasing lock: {e}")


# Global lock instance
_process_lock: Optional[ProcessLock] = None


def get_process_lock() -> ProcessLock:
    """Get the global process lock instance."""
    global _process_lock
    if _process_lock is None:
        _process_lock = ProcessLock()
    return _process_lock


def acquire_lock(timeout: float = 0) -> bool:
    """
    Convenience function to acquire process lock.
    
    Args:
        timeout: Maximum time to wait for lock (0 = non-blocking)
        
    Returns:
        True if lock acquired, False otherwise
    """
    return get_process_lock().acquire(timeout=timeout)


def release_lock():
    """Convenience function to release process lock."""
    get_process_lock().release()


def is_another_instance_running() -> bool:
    """Check if another instance is running without acquiring lock."""
    return ProcessLock.get_running_pid() is not None
