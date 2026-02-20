"""
Storage backends for YTBot.

This module provides storage abstraction layers for persisting downloaded content.
It supports multiple storage backends including local filesystem and Nextcloud
cloud storage, with automatic failover and space management capabilities.

The storage layer is designed to be extensible, allowing new storage backends
to be added without modifying the core application logic.

Exported Components:
    LocalStorageManager: class
        Manager for local file storage with automatic cleanup and space
        management. Provides file saving, retrieval, deletion, and cleanup
        of expired files based on configurable retention periods.

    local_storage_manager: LocalStorageManager
        Global singleton instance of LocalStorageManager for convenient access
        throughout the application.

    NextcloudStorage: class
        Nextcloud storage backend using WebDAV protocol. Provides file upload,
        connection testing, and automatic retry with exponential backoff for
        resilient cloud storage operations.

Features:
    - Automatic space management and capacity limits
    - Configurable file retention and cleanup
    - WebDAV-based Nextcloud integration
    - Upload verification and retry logic
    - Directory organization by date and content type

Example:
    >>> from ytbot.storage import LocalStorageManager, NextcloudStorage
    >>>
    >>> # Local storage usage
    >>> local = LocalStorageManager()
    >>>
    >>> # Check available space
    >>> available_mb = local.get_available_space_mb()
    >>> print(f"Available space: {available_mb:.1f} MB")
    >>>
    >>> # Save a file
    >>> local_path = local.save_file_locally("/tmp/video.mp4", "my_video.mp4")
    >>>
    >>> # Cleanup old files
    >>> cleanup_result = local.cleanup_old_files()
    >>> print(f"Removed {cleanup_result['files_removed']} files")
    >>>
    >>> # Nextcloud storage usage
    >>> nextcloud = NextcloudStorage()
    >>>
    >>> # Check connection
    >>> if nextcloud.check_connection():
    ...     # Upload file
    ...     url = nextcloud.upload_file(
    ...         "/tmp/video.mp4",
    ...         "/YTBot/Videos/my_video.mp4"
    ...     )
    ...     print(f"Uploaded to: {url}")
"""

from .local_storage import LocalStorageManager, local_storage_manager
from .nextcloud_storage import NextcloudStorage

__all__ = ["LocalStorageManager", "local_storage_manager", "NextcloudStorage"]
