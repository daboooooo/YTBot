"""
Download service for YTBot

Handles content downloading from various platforms through the platform manager.
"""

import asyncio
from typing import Optional, Dict, Any, Callable
from pathlib import Path

from ..core.config import CONFIG
from ..core.logger import get_logger
from ..platforms.base import PlatformManager, ContentType, DownloadResult

logger = get_logger(__name__)


class DownloadService:
    """
    Download service that coordinates platform handlers and manages download operations
    """

    def __init__(self):
        self.platform_manager = PlatformManager()
        self._setup_platforms()
        self._active_downloads: Dict[str, asyncio.Task] = {}

    def _setup_platforms(self):
        """Register available platform handlers"""
        from ..platforms.youtube import YouTubeHandler
        from ..platforms.twitter import TwitterHandler

        youtube_handler = YouTubeHandler()
        self.platform_manager.register_handler(youtube_handler)

        twitter_handler = TwitterHandler()
        self.platform_manager.register_handler(twitter_handler)

        logger.info(f"Registered platforms: {self.platform_manager.get_supported_platforms()}")

    def can_handle_url(self, url: str) -> bool:
        """Check if any registered platform can handle the URL"""
        return self.platform_manager.can_handle_url(url)

    def get_supported_platforms(self) -> list:
        """Get list of supported platforms"""
        return self.platform_manager.get_supported_platforms()

    async def get_content_info(self, url: str) -> Optional[Dict[str, Any]]:
        """
        Get information about content without downloading

        Args:
            url: Content URL

        Returns:
            dict: Content information or None if unavailable
        """
        handler = self.platform_manager.get_handler(url)
        if not handler:
            logger.warning(f"No handler found for URL: {url}")
            return None

        try:
            content_info = await handler.get_content_info(url)
            if content_info:
                return {
                    "url": content_info.url,
                    "title": content_info.title,
                    "description": content_info.description,
                    "duration": content_info.duration,
                    "content_type": content_info.content_type.value,
                    "thumbnail_url": content_info.thumbnail_url,
                    "uploader": content_info.uploader,
                    "upload_date": content_info.upload_date,
                }
            return None
        except Exception as e:
            logger.error(f"Failed to get content info for {url}: {e}")
            return None

    async def download_content(
        self,
        url: str,
        content_type: str = "video",
        progress_callback: Optional[Callable] = None,
        download_id: Optional[str] = None,
        format_id: Optional[str] = None
    ) -> DownloadResult:
        """
        Download content from URL

        Args:
            url: Content URL to download
            content_type: Type of content ("video", "audio", etc.)
            progress_callback: Optional progress callback function
            download_id: Optional download ID for tracking
            format_id: Optional specific format ID to download

        Returns:
            DownloadResult: Result of the download operation
        """
        if download_id is None:
            download_id = f"{hash(url)}_{content_type}"

        try:
            handler = self.platform_manager.get_handler(url)
            if not handler:
                return DownloadResult(
                    success=False,
                    error_message=f"No handler available for URL: {url}"
                )

            # Convert content type string to enum
            try:
                content_type_enum = ContentType(content_type)
            except ValueError:
                return DownloadResult(
                    success=False,
                    error_message=f"Unsupported content type: {content_type}"
                )

            logger.info(f"Starting download: {url} ({content_type})")

            # Perform download
            result = await handler.download_content(
                url=url,
                content_type=content_type_enum,
                progress_callback=progress_callback,
                format_id=format_id
            )

            if result.success:
                logger.info(f"Download completed successfully: {url}")
            else:
                logger.error(f"Download failed: {url} - {result.error_message}")

            return result

        except Exception as e:
            logger.error(f"Download error for {url}: {e}")
            return DownloadResult(
                success=False,
                error_message=f"Download error: {str(e)}"
            )

    def cancel_download(self, download_id: str) -> bool:
        """
        Cancel an active download

        Args:
            download_id: ID of the download to cancel

        Returns:
            bool: True if download was cancelled, False if not found
        """
        if download_id in self._active_downloads:
            task = self._active_downloads[download_id]
            if not task.done():
                task.cancel()
                logger.info(f"Cancelled download: {download_id}")
                return True

        logger.warning(f"Download not found for cancellation: {download_id}")
        return False

    async def get_supported_formats(self, url: str) -> list:
        """
        Get available download formats for a URL

        Args:
            url: Content URL

        Returns:
            list: Available formats
        """
        handler = self.platform_manager.get_handler(url)
        if not handler:
            return []

        try:
            return await handler.get_supported_formats(url)
        except Exception as e:
            logger.error(f"Failed to get formats for {url}: {e}")
            return []