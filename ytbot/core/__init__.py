"""
Core functionality for YTBot.

This module provides the foundational components for the YTBot application,
including configuration management and logging utilities. It serves as the
central point for accessing application-wide settings and logger instances.

Exported Components:
    CONFIG: dict
        Main configuration dictionary containing all application settings
        organized by category (telegram, nextcloud, local_storage, download,
        log, app, monitor, security).
    validate_config: callable
        Function to validate configuration and return a list of missing
        required settings.
    get_logger: callable
        Function to obtain a logger instance for a specific module or the
        default 'ytbot' logger.
    setup_exception_handler: callable
        Function to set up a global exception handler for better error
        reporting and logging of uncaught exceptions.

Example:
    >>> from ytbot.core import CONFIG, validate_config, get_logger
    >>>
    >>> # Validate configuration
    >>> errors = validate_config()
    >>> if errors:
    ...     print(f"Configuration errors: {errors}")
    >>>
    >>> # Access configuration values
    >>> token = CONFIG['telegram']['token']
    >>> log_level = CONFIG['log']['level']
    >>>
    >>> # Get a logger for your module
    >>> logger = get_logger(__name__)
    >>> logger.info("Application started")
"""

from .config import CONFIG, validate_config
from .logger import get_logger, setup_exception_handler

__all__ = ["CONFIG", "validate_config", "get_logger", "setup_exception_handler"]
