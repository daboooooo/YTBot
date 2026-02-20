"""
Platform-specific content handlers for YTBot.

This module provides a unified interface for handling content downloads from
various platforms. Each platform handler implements a common interface that
supports URL validation, content information extraction, and downloading.

The module uses a registry pattern where platform handlers are registered
and automatically selected based on the URL being processed.

Exported Components:
    YouTubeHandler: class
        Handler for YouTube content including videos, audio, and playlists.
        Supports multiple formats and quality selection, with automatic
        format detection and best quality selection.

Supported Platforms:
    - YouTube (youtube.com, youtu.be)
        - Video downloads (various qualities up to 1080p)
        - Audio downloads (MP3, M4A, Opus)
        - Playlist support

Example:
    >>> from ytbot.platforms import YouTubeHandler
    >>>
    >>> handler = YouTubeHandler()
    >>>
    >>> # Check if URL is supported
    >>> if handler.can_handle("https://www.youtube.com/watch?v=dQw4w9WgXcQ"):
    ...     # Get content information
    ...     info = await handler.get_content_info(url)
    ...     print(f"Title: {info.title}")
    ...
    ...     # Download content
    ...     result = await handler.download_content(url, ContentType.VIDEO)
    ...     if result.success:
    ...         print(f"Downloaded to: {result.file_path}")
"""

from .youtube import YouTubeHandler

__all__ = ["YouTubeHandler"]
