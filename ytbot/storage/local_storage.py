"""
Local storage backend for YTBot

Provides local file storage with automatic cleanup and space management.
"""

import os
import shutil
import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, Any

from ..core.config import CONFIG
from ..core.logger import get_logger

logger = get_logger(__name__)


class LocalStorageManager:
    """Local storage manager with space management and automatic cleanup"""

    def __init__(self):
        self.storage_path = Path(CONFIG['local_storage']['path'])
        self.max_size_mb = CONFIG['local_storage']['max_size_mb']
        self.cleanup_after_days = CONFIG['local_storage']['cleanup_after_days']
        self.enabled = CONFIG['local_storage']['enabled']

        # Ensure storage directory exists
        if self.enabled:
            self._ensure_storage_directory()

    def _ensure_storage_directory(self):
        """Ensure storage directory exists"""
        try:
            self.storage_path.mkdir(parents=True, exist_ok=True)
            logger.info(f"Local storage directory ensured: {self.storage_path}")
        except Exception as e:
            logger.error(f"Failed to create local storage directory: {e}")
            self.enabled = False

    def get_available_space_mb(self) -> float:
        """Get available disk space in MB"""
        try:
            stat = shutil.disk_usage(self.storage_path)
            return stat.free / (1024 * 1024)
        except Exception as e:
            logger.error(f"Failed to get disk space: {e}")
            return 0.0

    def get_storage_usage_mb(self) -> float:
        """Get current storage usage in MB"""
        try:
            total_size = 0
            for file_path in self.storage_path.rglob('*'):
                if file_path.is_file():
                    total_size += file_path.stat().st_size
            return total_size / (1024 * 1024)
        except Exception as e:
            logger.error(f"Failed to calculate storage usage: {e}")
            return 0.0

    def can_store_file(self, file_size_mb: float) -> bool:
        """Check if there's enough space to store a file"""
        if not self.enabled:
            return False

        available_space = self.get_available_space_mb()
        current_usage = self.get_storage_usage_mb()

        # Check capacity limits and available space
        if (current_usage + file_size_mb) > self.max_size_mb:
            logger.warning(f"Storage capacity exceeded: {current_usage:.1f}MB used + "
                          f"{file_size_mb:.1f}MB needed > {self.max_size_mb}MB limit")
            return False

        if file_size_mb > available_space:
            logger.warning(f"Insufficient disk space: {available_space:.1f}MB available, "
                          f"{file_size_mb:.1f}MB needed")
            return False

        return True

    def save_file_locally(self, source_path: str, filename: str) -> Optional[str]:
        """
        Save a file or directory to local storage

        Args:
            source_path: Source file or directory path
            filename: Target filename

        Returns:
            str: Local file path if successful, None otherwise
        """
        if not self.enabled:
            logger.warning("Local storage is disabled")
            return None

        try:
            source = Path(source_path)

            if source.is_dir():
                return self._save_directory(source, filename)
            else:
                return self._save_file(source, filename)

        except Exception as e:
            logger.error(f"Failed to save file to local storage: {e}")
            return None

    def _save_file(self, source_path: Path, filename: str) -> Optional[str]:
        """Save a single file to local storage"""
        try:
            file_size = os.path.getsize(source_path)
            file_size_mb = file_size / (1024 * 1024)

            if not self.can_store_file(file_size_mb):
                return None

            date_folder = datetime.now().strftime("%Y-%m")
            target_dir = self.storage_path / date_folder
            target_dir.mkdir(exist_ok=True)

            target_path = target_dir / filename

            if target_path.exists():
                timestamp = datetime.now().strftime("%H%M%S")
                name, ext = os.path.splitext(filename)
                target_path = target_dir / f"{name}_{timestamp}{ext}"

            shutil.copy2(source_path, target_path)

            logger.info(f"File saved to local storage: {target_path} "
                       f"({file_size_mb:.1f}MB)")
            return str(target_path)

        except Exception as e:
            logger.error(f"Failed to save file: {e}")
            return None

    def _save_directory(self, source_dir: Path, filename: str) -> Optional[str]:
        """Save a directory (with images) to local storage"""
        try:
            html_files = list(source_dir.glob("*.html"))
            if not html_files:
                logger.error(f"No HTML file found in directory: {source_dir}")
                return None

            html_file = html_files[0]

            date_folder = datetime.now().strftime("%Y-%m")
            target_dir = self.storage_path / date_folder
            target_dir.mkdir(exist_ok=True)

            name, ext = os.path.splitext(filename)
            if not ext:
                ext = ".html"
                filename = name + ext

            target_path = target_dir / filename

            if target_path.exists():
                timestamp = datetime.now().strftime("%H%M%S")
                target_path = target_dir / f"{name}_{timestamp}{ext}"

            # Create a directory for this tweet's files
            # Use the filename (without extension) as directory name
            tweet_dir = target_path.parent / target_path.stem
            tweet_dir.mkdir(exist_ok=True)

            # Copy HTML file into the tweet directory
            html_target = tweet_dir / target_path.name
            shutil.copy2(html_file, html_target)

            # Update target_path to point to the HTML inside tweet directory
            target_path = html_target

            # Copy images to tweet directory
            images_source = source_dir / "images"
            if images_source.exists():
                images_target = tweet_dir / "images"
                images_target.mkdir(exist_ok=True)

                for img_file in images_source.iterdir():
                    if img_file.is_file():
                        shutil.copy2(img_file, images_target / img_file.name)

            # Copy videos to tweet directory
            videos_source = source_dir / "videos"
            if videos_source.exists():
                videos_target = tweet_dir / "videos"
                videos_target.mkdir(exist_ok=True)

                for video_file in videos_source.iterdir():
                    if video_file.is_file():
                        shutil.copy2(video_file, videos_target / video_file.name)

            has_images = images_source.exists()
            has_videos = videos_source.exists()
            if has_images or has_videos:
                logger.info(
                    f"Directory saved to local storage: {target_path} "
                    f"(images: {has_images}, videos: {has_videos})"
                )
            else:
                logger.info(f"File saved to local storage: {target_path}")

            return str(target_path)

        except Exception as e:
            logger.error(f"Failed to save directory: {e}")
            return None

    def cleanup_old_files(self) -> Dict[str, Any]:
        """
        Clean up expired local files

        Returns:
            dict: Cleanup statistics
        """
        if not self.enabled:
            return {"cleaned": False, "reason": "Local storage disabled"}

        cleanup_stats = {
            "files_removed": 0,
            "space_freed_mb": 0.0,
            "errors": []
        }

        try:
            cutoff_date = datetime.now() - timedelta(days=self.cleanup_after_days)

            for file_path in self.storage_path.rglob('*'):
                if not file_path.is_file():
                    continue

                try:
                    # Get file modification time
                    file_mtime = datetime.fromtimestamp(file_path.stat().st_mtime)

                    # Delete if expired
                    if file_mtime < cutoff_date:
                        file_size = file_path.stat().st_size
                        file_size_mb = file_size / (1024 * 1024)

                        file_path.unlink()

                        cleanup_stats["files_removed"] += 1
                        cleanup_stats["space_freed_mb"] += file_size_mb

                        logger.debug(f"Deleted expired file: {file_path} "
                                   f"({file_size_mb:.1f}MB)")

                except Exception as e:
                    cleanup_stats["errors"].append(f"Failed to delete {file_path}: {str(e)}")
                    logger.error(f"Failed to delete file: {file_path}, error: {e}")

            logger.info(f"Local storage cleanup completed: "
                       f"{cleanup_stats['files_removed']} files removed, "
                       f"{cleanup_stats['space_freed_mb']:.1f}MB freed")

        except Exception as e:
            cleanup_stats["errors"].append(f"Cleanup failed: {str(e)}")
            logger.error(f"Local storage cleanup failed: {e}")

        return cleanup_stats

    def get_file_info(self, file_path: str) -> Optional[Dict[str, Any]]:
        """Get information about a local file"""
        try:
            path = Path(file_path)
            if not path.exists():
                return None

            stat = path.stat()
            return {
                "path": str(path),
                "size": stat.st_size,
                "size_mb": stat.st_size / (1024 * 1024),
                "created": datetime.fromtimestamp(stat.st_ctime),
                "modified": datetime.fromtimestamp(stat.st_mtime),
                "filename": path.name
            }
        except Exception as e:
            logger.error(f"Failed to get file info: {e}")
            return None

    def delete_file(self, file_path: str) -> bool:
        """Delete a local file"""
        try:
            path = Path(file_path)
            if path.exists():
                path.unlink()
                logger.info(f"Local file deleted: {file_path}")
                return True
            else:
                logger.warning(f"File not found, cannot delete: {file_path}")
                return False
        except Exception as e:
            logger.error(f"Failed to delete local file: {e}")
            return False


# Global instance
local_storage_manager = LocalStorageManager()


async def cleanup_local_storage() -> Dict[str, Any]:
    """Async wrapper for local storage cleanup"""
    return await asyncio.to_thread(local_storage_manager.cleanup_old_files)


def save_file_locally(source_path: str, filename: str) -> Optional[str]:
    """Convenience function to save file locally"""
    return local_storage_manager.save_file_locally(source_path, filename)


def get_local_storage_info() -> Dict[str, Any]:
    """Get local storage information"""
    manager = local_storage_manager
    if not manager.enabled:
        return {"enabled": False}

    return {
        "enabled": True,
        "path": str(manager.storage_path),
        "usage_mb": manager.get_storage_usage_mb(),
        "available_space_mb": manager.get_available_space_mb(),
        "max_size_mb": manager.max_size_mb,
        "cleanup_after_days": manager.cleanup_after_days
    }