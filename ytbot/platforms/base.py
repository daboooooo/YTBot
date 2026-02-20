"""
Base platform handler for extensible content platform support

This module provides the base classes and interfaces for adding new content platforms
like YouTube, Twitter/X, Instagram, etc.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List
from dataclasses import dataclass
from enum import Enum


class ContentType(Enum):
    """Supported content types"""
    VIDEO = "video"
    AUDIO = "audio"
    IMAGE = "image"
    TEXT = "text"
    PLAYLIST = "playlist"


@dataclass
class ContentInfo:
    """Information about downloadable content"""
    url: str
    title: str
    description: Optional[str] = None
    duration: Optional[int] = None  # seconds
    content_type: ContentType = ContentType.VIDEO
    thumbnail_url: Optional[str] = None
    uploader: Optional[str] = None
    upload_date: Optional[str] = None
    file_size_estimate: Optional[int] = None  # bytes
    formats: Optional[List[Dict[str, Any]]] = None
    metadata: Optional[Dict[str, Any]] = None


@dataclass
class DownloadResult:
    """Result of a download operation"""
    success: bool
    file_path: Optional[str] = None
    content_info: Optional[ContentInfo] = None
    error_message: Optional[str] = None
    cancelled: bool = False


class PlatformHandler(ABC):
    """
    Abstract base class for content platform handlers

    All platform handlers (YouTube, Twitter, etc.) must inherit from this class
    and implement the required methods.
    """

    def __init__(self, name: str):
        self.name = name
        self.supported_content_types: List[ContentType] = []

    @abstractmethod
    def can_handle(self, url: str) -> bool:
        """
        Check if this handler can process the given URL

        Args:
            url: The URL to check

        Returns:
            bool: True if this handler can process the URL
        """
        pass

    @abstractmethod
    async def get_content_info(self, url: str) -> Optional[ContentInfo]:
        """
        Get information about the content without downloading

        Args:
            url: The content URL

        Returns:
            ContentInfo: Information about the content, or None if unavailable
        """
        pass

    @abstractmethod
    async def download_content(
        self,
        url: str,
        content_type: ContentType,
        progress_callback=None,
        format_id: Optional[str] = None
    ) -> DownloadResult:
        """
        Download content from the platform

        Args:
            url: The content URL
            content_type: The type of content to download
            progress_callback: Optional callback for progress updates
            format_id: Optional specific format ID to download

        Returns:
            DownloadResult: Result of the download operation
        """
        pass

    @abstractmethod
    def get_supported_formats(self, url: str) -> List[Dict[str, Any]]:
        """
        Get available download formats for the content

        Args:
            url: The content URL

        Returns:
            List[Dict]: Available formats with quality, size, etc.
        """
        pass

    def validate_url(self, url: str) -> bool:
        """
        Basic URL validation - can be overridden by subclasses

        Args:
            url: The URL to validate

        Returns:
            bool: True if URL appears valid
        """
        if not url or not isinstance(url, str):
            return False

        url = url.strip()
        return len(url) > 10 and (url.startswith('http://') or url.startswith('https://'))


class PlatformManager:
    """
    Manager for all platform handlers

    Handles registration and routing of content to appropriate platform handlers
    """

    def __init__(self):
        self.handlers: List[PlatformHandler] = []

    def register_handler(self, handler: PlatformHandler):
        """Register a new platform handler"""
        self.handlers.append(handler)

    def get_handler(self, url: str) -> Optional[PlatformHandler]:
        """
        Get the appropriate handler for a URL

        Args:
            url: The content URL

        Returns:
            PlatformHandler: The handler that can process this URL, or None
        """
        for handler in self.handlers:
            if handler.can_handle(url):
                return handler
        return None

    def get_supported_platforms(self) -> List[str]:
        """Get list of all supported platform names"""
        return [handler.name for handler in self.handlers]

    def can_handle_url(self, url: str) -> bool:
        """Check if any registered handler can process this URL"""
        return self.get_handler(url) is not None
