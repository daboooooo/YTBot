"""
Storage service for YTBot

Provides unified interface for local and cloud storage backends.
"""

import os
import asyncio
from typing import Dict, Any, Optional
from datetime import datetime

from ..core.config import CONFIG
from ..core.enhanced_logger import get_logger, log_function_entry_exit
from ..storage.local_storage import LocalStorageManager, get_local_storage_info
from ..storage.nextcloud_storage import NextcloudStorage
from ..storage.cache_manager import get_cache_manager

logger = get_logger(__name__)


class StorageService:
    """
    Unified storage service that manages both local and cloud storage backends

    Provides automatic failover from Nextcloud to local storage when Nextcloud is unavailable.
    Includes cache queue support for automatic retry when Nextcloud recovers.
    """

    def __init__(self):
        self.local_storage = LocalStorageManager()
        self.nextcloud_storage = NextcloudStorage()
        self.cache_manager = get_cache_manager()
        self._nextcloud_available = None  # Will be determined on first use
        self._retry_task: Optional[asyncio.Task] = None
        self._retry_interval = 300  # 5 minutes default retry interval
        self._is_retrying = False

    @property
    def nextcloud_available(self) -> bool:
        """Check if Nextcloud is available"""
        if self._nextcloud_available is None:
            # Test Nextcloud connection on first use
            self._nextcloud_available = self.nextcloud_storage.check_connection()
        return self._nextcloud_available

    def mark_nextcloud_unavailable(self):
        """Mark Nextcloud as unavailable (used when upload fails) with detailed logging"""
        logger.warning("⚠️  Nextcloud marked as unavailable, will use local storage")
        logger.info("🔄 Storage failover: Switching to local storage backend")
        self._nextcloud_available = False

    @log_function_entry_exit(logger)
    async def store_file(
        self,
        source_path: str,
        filename: str,
        content_type: str = "media"
    ) -> Dict[str, Any]:
        """
        Store a file using the best available storage backend with detailed logging

        Args:
            source_path: Local file path to store
            filename: Target filename
            content_type: Type of content (for organization)

        Returns:
            dict: Storage result with status and location info
        """
        logger.info(f"📁 Storing file: {filename} (type: {content_type})")
        logger.debug(f"Source path: {source_path}")
        logger.debug(f"Nextcloud available: {self.nextcloud_available}")

        result = {
            "success": False,
            "storage_type": None,
            "file_path": None,
            "file_url": None,
            "error": None,
            "cached": False
        }

        # Validate source file exists
        if not os.path.exists(source_path):
            error_msg = f"Source file does not exist: {source_path}"
            logger.error(f"❌ {error_msg}")
            result["error"] = error_msg
            return result

        # Get file size for logging
        try:
            file_size = os.path.getsize(source_path)
            logger.debug(f"File size: {file_size} bytes ({file_size / 1024 / 1024:.2f} MB)")
        except Exception as e:
            logger.warning(f"⚠️  Could not get file size: {e}")

        # Try Nextcloud first if available
        logger.info("🚀 Starting storage process...")
        if self.nextcloud_available:
            logger.info("☁️  Attempting Nextcloud storage...")
            try:
                # Build remote path with content type organization
                remote_dir = CONFIG['nextcloud']['upload_dir']
                if not remote_dir.startswith('/'):
                    remote_dir = f'/{remote_dir}'

                # Organize by content type
                media_type_dir = content_type.capitalize()
                remote_path = f"{remote_dir}/{media_type_dir}/{filename}"

                logger.debug(f"Remote path: {remote_path}")
                logger.debug("Uploading to Nextcloud...")

                file_url = self.nextcloud_storage.upload_file(source_path, remote_path)

                if file_url:
                    logger.info(f"✅ File stored in Nextcloud: {file_url}")
                    result.update({
                        "success": True,
                        "storage_type": "nextcloud",
                        "file_url": file_url,
                        "file_path": remote_path
                    })
                    return result
                else:
                    logger.warning("⚠️  Nextcloud upload returned no URL")
                    # Nextcloud upload failed, mark as unavailable
                    self.mark_nextcloud_unavailable()

            except Exception as e:
                logger.error(f"❌ Nextcloud upload failed: {e}")
                logger.exception("Nextcloud upload error details:")
                self.mark_nextcloud_unavailable()
        else:
            logger.info("☁️  Nextcloud not available, skipping to local storage")

        # Fallback to local storage
        if CONFIG['local_storage']['enabled']:
            logger.info("💾 Attempting local storage...")
            try:
                logger.debug("Saving file locally...")
                local_path = self.local_storage.save_file_locally(source_path, filename)

                if local_path:
                    logger.info(f"✅ File stored locally: {local_path}")

                    # Add to cache queue for later retry
                    cache_added = self.cache_manager.add_to_cache(
                        file_path=local_path,
                        filename=filename,
                        content_type=content_type,
                        metadata={
                            "original_source": source_path,
                            "timestamp": datetime.now().isoformat()
                        }
                    )

                    if cache_added:
                        logger.info("📋 File added to cache queue for later upload to Nextcloud")
                        result["cached"] = True
                    else:
                        logger.warning("⚠️  Failed to add file to cache queue")

                    result.update({
                        "success": True,
                        "storage_type": "local",
                        "file_path": local_path
                    })
                    return result
                else:
                    logger.error("❌ Local storage failed - no path returned")
                    result["error"] = "Local storage failed"

            except Exception as e:
                logger.error(f"❌ Local storage failed: {e}")
                logger.exception("Local storage error details:")
                result["error"] = f"Local storage error: {e}"
        else:
            logger.error("❌ No storage backends available")
            result["error"] = "No storage backends available"

        logger.error(f"❌ Storage failed: {result['error']}")
        return result

    @log_function_entry_exit(logger)
    def get_storage_info(self) -> Dict[str, Any]:
        """Get information about all storage backends with detailed logging"""
        logger.info("📊 Getting storage information...")

        info = {
            "nextcloud": {
                "available": self.nextcloud_available,
                "url": CONFIG['nextcloud']['url'] if CONFIG['nextcloud']['url'] else None
            },
            "local_storage": get_local_storage_info()
        }

        nc_available = 'Available' if info['nextcloud']['available'] else 'Unavailable'
        logger.info(f"☁️  Nextcloud: {nc_available}")
        if info['nextcloud']['url']:
            logger.debug(f"Nextcloud URL: {info['nextcloud']['url']}")

        local_info = info['local_storage']
        local_enabled = 'Enabled' if local_info.get('enabled', False) else 'Disabled'
        logger.info(f"💾 Local Storage: {local_enabled}")
        if local_info.get('enabled'):
            logger.debug(f"Local path: {local_info.get('path', 'Unknown')}")
            logger.debug(f"Usage: {local_info.get('usage_mb', 0):.1f} MB")
            space = local_info.get('available_space_mb', 0)
            logger.debug(f"Available space: {space:.1f} MB")

        return info

    @log_function_entry_exit(logger)
    def cleanup_expired_files(self) -> Dict[str, Any]:
        """Clean up expired files from local storage with detailed logging"""
        logger.info("🧹 Starting cleanup of expired files...")

        if CONFIG['local_storage']['enabled']:
            logger.debug("Local storage is enabled, proceeding with cleanup...")
            result = self.local_storage.cleanup_old_files()

            if result.get('success', False):
                logger.info(f"✅ Cleanup completed: {result.get('files_removed', 0)} files removed")
                logger.info(f"💾 Space freed: {result.get('space_freed_mb', 0):.1f} MB")
            else:
                error_msg = result.get('error', 'Unknown error')
                logger.warning(f"⚠️  Cleanup completed with issues: {error_msg}")

            return result
        else:
            logger.warning("⚠️  Local storage is disabled, skipping cleanup")
            return {"cleaned": False, "reason": "Local storage disabled"}

    @log_function_entry_exit(logger)
    def delete_file(self, file_path: str, storage_type: str) -> bool:
        """
        Delete a file from the specified storage with detailed logging

        Args:
            file_path: Path to the file
            storage_type: Type of storage ('local' or 'nextcloud')

        Returns:
            bool: Success status
        """
        logger.info(f"🗑️  Deleting file: {file_path} from {storage_type}")

        try:
            if storage_type == "local":
                logger.debug("Deleting from local storage...")
                success = self.local_storage.delete_file(file_path)
                if success:
                    logger.info(f"✅ File deleted from local storage: {file_path}")
                else:
                    logger.warning(f"⚠️  Failed to delete file from local storage: {file_path}")
                return success
            elif storage_type == "nextcloud":
                # Nextcloud file deletion would be implemented here
                logger.warning("⚠️  Nextcloud file deletion not yet implemented")
                return False
            else:
                logger.error(f"❌ Unknown storage type: {storage_type}")
                return False
        except Exception as e:
            logger.error(f"❌ Failed to delete file from {storage_type}: {e}")
            logger.exception("File deletion error details:")
            return False

    @log_function_entry_exit(logger)
    async def retry_cached_files(self) -> Dict[str, Any]:
        """
        Retry uploading cached files to Nextcloud

        Checks Nextcloud connection and uploads files from cache queue.
        Successfully uploaded files are removed from cache.

        Returns:
            dict: Retry operation results
        """
        logger.info("🔄 Starting cache file retry process...")

        result = {
            "success": True,
            "nextcloud_available": False,
            "files_processed": 0,
            "files_uploaded": 0,
            "files_failed": 0,
            "errors": []
        }

        # Check Nextcloud connection
        logger.debug("Checking Nextcloud connection...")
        if not self.nextcloud_storage.check_connection():
            logger.warning("⚠️  Nextcloud still unavailable, skipping retry")
            result["success"] = False
            result["errors"].append("Nextcloud unavailable")
            return result

        result["nextcloud_available"] = True
        logger.info("✅ Nextcloud connection restored")

        # Mark Nextcloud as available again
        self._nextcloud_available = True

        # Get cache queue
        cache_queue = self.cache_manager.get_cache_queue()

        if not cache_queue:
            logger.info("📋 Cache queue is empty, nothing to retry")
            return result

        logger.info(f"📋 Found {len(cache_queue)} files in cache queue")

        # Process each cached file
        for cache_entry in cache_queue:
            file_path = cache_entry.get('file_path')
            filename = cache_entry.get('filename')
            content_type = cache_entry.get('content_type', 'media')

            result["files_processed"] += 1

            logger.info(
                f"📤 Retrying file {result['files_processed']}/{len(cache_queue)}: "
                f"{filename}"
            )

            # Check if file still exists
            if not file_path or not os.path.exists(file_path):
                logger.warning(f"⚠️  Cached file no longer exists: {file_path}")
                # Remove from cache
                self.cache_manager.remove_from_cache(file_path)
                result["files_failed"] += 1
                result["errors"].append(f"File not found: {filename}")
                continue

            try:
                # Build remote path
                remote_dir = CONFIG['nextcloud']['upload_dir']
                if not remote_dir.startswith('/'):
                    remote_dir = f'/{remote_dir}'

                media_type_dir = content_type.capitalize()
                remote_path = f"{remote_dir}/{media_type_dir}/{filename}"

                logger.debug(f"Uploading to: {remote_path}")

                # Upload to Nextcloud
                file_url = self.nextcloud_storage.upload_file(file_path, remote_path)

                if file_url:
                    logger.info(f"✅ Successfully uploaded: {file_url}")

                    # Remove from cache queue
                    self.cache_manager.remove_from_cache(file_path)

                    # Optionally delete local file after successful upload
                    if CONFIG.get('local_storage', {}).get('delete_after_upload', False):
                        try:
                            os.remove(file_path)
                            logger.debug(f"Deleted local cache file: {file_path}")
                        except Exception as e:
                            logger.warning(f"Failed to delete local cache file: {e}")

                    result["files_uploaded"] += 1
                else:
                    logger.warning(f"⚠️  Upload failed for: {filename}")
                    result["files_failed"] += 1
                    result["errors"].append(f"Upload failed: {filename}")

            except Exception as e:
                logger.error(f"❌ Error uploading cached file {filename}: {e}")
                result["files_failed"] += 1
                result["errors"].append(f"{filename}: {str(e)}")

        logger.info(
            f"✅ Cache retry completed: {result['files_uploaded']} uploaded, "
            f"{result['files_failed']} failed out of {result['files_processed']} processed"
        )

        return result

    @log_function_entry_exit(logger)
    def get_cache_status(self) -> Dict[str, Any]:
        """
        Get cache queue status and statistics

        Returns:
            dict: Cache status information
        """
        logger.info("📊 Getting cache status...")

        cache_stats = self.cache_manager.get_cache_stats()

        status = {
            "cache_enabled": True,
            "total_items": cache_stats.get("total_items", 0),
            "files_exist": cache_stats.get("files_exist", 0),
            "files_missing": cache_stats.get("files_missing", 0),
            "total_size_mb": cache_stats.get("total_size_mb", 0.0),
            "content_types": cache_stats.get("content_types", {}),
            "cache_dir": cache_stats.get("cache_dir"),
            "nextcloud_available": self.nextcloud_available,
            "auto_retry_active": self._is_retrying
        }

        logger.info(
            f"📋 Cache status: {status['total_items']} items, "
            f"{status['total_size_mb']:.2f} MB"
        )
        logger.debug(f"Content types: {status['content_types']}")

        return status

    @log_function_entry_exit(logger)
    async def start_background_retry_task(self, interval_seconds: int = 300):
        """
        Start background task to automatically retry cached files

        Args:
            interval_seconds: Interval between retry attempts (default: 300 = 5 minutes)
        """
        if self._is_retrying:
            logger.warning("⚠️  Background retry task is already running")
            return

        logger.info(f"🔄 Starting background retry task (interval: {interval_seconds}s)")
        self._retry_interval = interval_seconds
        self._is_retrying = True

        # Create background task
        self._retry_task = asyncio.create_task(self._background_retry_loop())

        logger.info("✅ Background retry task started")

    @log_function_entry_exit(logger)
    async def stop_background_retry_task(self):
        """Stop background retry task"""
        if not self._is_retrying or not self._retry_task:
            logger.warning("⚠️  Background retry task is not running")
            return

        logger.info("🛑 Stopping background retry task...")
        self._is_retrying = False

        # Cancel the task
        if self._retry_task:
            self._retry_task.cancel()
            try:
                await self._retry_task
            except asyncio.CancelledError:
                pass
            self._retry_task = None

        logger.info("✅ Background retry task stopped")

    async def _background_retry_loop(self):
        """
        Background loop that periodically checks and retries cached files

        This is an internal method that runs as an asyncio task.
        """
        logger.info("🔄 Background retry loop started")

        while self._is_retrying:
            try:
                # Wait for the specified interval
                await asyncio.sleep(self._retry_interval)

                # Check if there are cached files
                cache_stats = self.cache_manager.get_cache_stats()

                if cache_stats.get("total_items", 0) > 0:
                    logger.info(
                        f"🔄 Auto-retry: Found {cache_stats['total_items']} cached files, "
                        "attempting upload..."
                    )

                    # Attempt to retry cached files
                    result = await self.retry_cached_files()

                    if result.get("success"):
                        logger.info(
                            f"✅ Auto-retry successful: {result['files_uploaded']} files uploaded"
                        )
                    else:
                        logger.debug("Auto-retry: Nextcloud still unavailable")
                else:
                    logger.debug("Auto-retry: No cached files to process")

            except asyncio.CancelledError:
                logger.info("Background retry loop cancelled")
                break
            except Exception as e:
                logger.error(f"❌ Error in background retry loop: {e}")
                # Continue running despite errors
                await asyncio.sleep(60)  # Wait a minute before retrying after error

        logger.info("🔄 Background retry loop ended")

    @log_function_entry_exit(logger)
    def get_storage_health(self) -> Dict[str, Any]:
        """
        Get comprehensive health status of all storage backends

        Returns:
            dict: Health status information
        """
        logger.info("🏥 Checking storage health...")

        health = {
            "timestamp": datetime.now().isoformat(),
            "nextcloud": {
                "available": self.nextcloud_available,
                "connected": self.nextcloud_storage.is_connected()
            },
            "local_storage": {
                "enabled": CONFIG['local_storage']['enabled'],
                "available_space_mb": (
                    self.local_storage.get_available_space_mb()
                    if CONFIG['local_storage']['enabled'] else 0
                ),
                "usage_mb": (
                    self.local_storage.get_storage_usage_mb()
                    if CONFIG['local_storage']['enabled'] else 0
                )
            },
            "cache": self.get_cache_status()
        }

        # Determine overall health status
        issues = []

        if not health["nextcloud"]["available"]:
            issues.append("Nextcloud unavailable")

        if health["cache"]["total_items"] > 0:
            issues.append(f"{health['cache']['total_items']} files in cache queue")

        if health["local_storage"]["enabled"]:
            available_space = health["local_storage"]["available_space_mb"]
            if available_space < 100:  # Less than 100MB
                issues.append(f"Low disk space: {available_space:.1f}MB")

        health["status"] = "healthy" if not issues else "degraded"
        health["issues"] = issues

        if issues:
            logger.warning(f"⚠️  Storage health issues: {', '.join(issues)}")
        else:
            logger.info("✅ Storage health: All systems operational")

        return health