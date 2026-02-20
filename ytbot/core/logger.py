"""
Logging configuration and utilities for YTBot
"""

import logging
import logging.handlers
import os
import sys
from typing import Optional

from .config import CONFIG


def setup_logger(name: str = 'ytbot') -> logging.Logger:
    """
    Set up logging system with console and file handlers.

    Args:
        name: Logger name

    Returns:
        logging.Logger: Configured logger
    """
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, CONFIG['log']['level']))

    # Prevent duplicate handlers
    if logger.handlers:
        return logger

    # Create formatter
    formatter = logging.Formatter(CONFIG['log']['format'])

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(getattr(logging, CONFIG['log']['level']))
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # File handler with rotation
    try:
        log_dir = os.path.dirname(CONFIG['log']['file'])
        if log_dir and not os.path.exists(log_dir):
            os.makedirs(log_dir)

        file_handler = logging.handlers.RotatingFileHandler(
            filename=CONFIG['log']['file'],
            maxBytes=CONFIG['log']['max_bytes'],
            backupCount=CONFIG['log']['backup_count'],
            encoding='utf-8'
        )
        file_handler.setLevel(getattr(logging, CONFIG['log']['level']))
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    except Exception as e:
        logger.warning(f"Failed to set up file logging: {e}")

    return logger


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """
    Get a logger instance.

    Args:
        name: Logger name, defaults to module name

    Returns:
        logging.Logger: Logger instance
    """
    if name is None:
        # Get caller's module name
        import inspect
        frame = inspect.currentframe()
        if frame and frame.f_back:
            name = frame.f_back.f_globals.get('__name__', 'ytbot')
        else:
            name = 'ytbot'

    return logging.getLogger(name)


def setup_exception_handler():
    """Set up global exception handler for better error reporting."""
    def handle_exception(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return

        logger = get_logger()
        logger.critical("Uncaught exception", exc_info=(exc_type, exc_value, exc_traceback))

    sys.excepthook = handle_exception