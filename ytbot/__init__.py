"""
YTBot - A multi-platform content download and management bot

A professional Python tool for downloading and managing content from various platforms
including YouTube, Twitter/X, and more.
"""

__version__ = "2.1.0"
__author__ = "YTBot Team"
__description__ = "Multi-platform content download and management bot"

from .core import config, logger
from .services import telegram_service

__all__ = [
    "config",
    "logger",
    "telegram_service",
    "__version__",
]
