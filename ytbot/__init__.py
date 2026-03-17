"""
YTBot - A multi-platform content download and management bot

A professional Python tool for downloading and managing content from various platforms
including YouTube, Twitter/X, and more.
"""

__version__ = "2.5.0"
__author__ = "horsen666@gmail.com"
__description__ = "Multi-platform content download and management bot"
__change_log__ = """
2.4.0 (2026-03-17)
- Large file download confirmation for YouTube videos (>500MB)
- Improved format detection and file size estimation
- Fixed format list parsing issues with yt-dlp

2.3.0 (2024-03-15)
- Large file download confirmation
- use __version__ in AppConfig
"""

from .core import config, logger
from .services import telegram_service

__all__ = [
    "config",
    "logger",
    "telegram_service",
    "__version__",
]
