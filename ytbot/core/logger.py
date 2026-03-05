"""
Logging configuration and utilities for YTBot
"""

import logging
import logging.handlers
import os
import sys
import inspect
from typing import Optional, Callable, Any, TypeVar, cast

from .config import get_config

F = TypeVar('F', bound=Callable[..., Any])


def setup_logger(name: str = 'ytbot') -> logging.Logger:
    """
    Set up logging system with console and file handlers.

    Args:
        name: Logger name

    Returns:
        Configured logger
    """
    config = get_config()
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, config.log.level))

    # Prevent duplicate handlers
    if logger.handlers:
        return logger

    # Create formatter
    formatter = logging.Formatter(config.log.format)

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(getattr(logging, config.log.level))
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # File handler with rotation
    try:
        log_dir = os.path.dirname(config.log.file)
        if log_dir and not os.path.exists(log_dir):
            os.makedirs(log_dir)

        file_handler = logging.handlers.RotatingFileHandler(
            filename=config.log.file,
            maxBytes=config.log.max_bytes,
            backupCount=config.log.backup_count,
            encoding='utf-8'
        )
        file_handler.setLevel(getattr(logging, config.log.level))
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
        Logger instance
    """
    if name is None:
        # Get caller's module name
        frame = inspect.currentframe()
        if frame and frame.f_back:
            name = frame.f_back.f_globals.get('__name__', 'ytbot')
        else:
            name = 'ytbot'

    return logging.getLogger(name)


def log_function_entry_exit(
    logger: logging.Logger,
    level: int = logging.DEBUG
) -> Callable[[F], F]:
    """
    Decorator to log function entry and exit.

    Args:
        logger: Logger to use
        level: Log level for entry/exit messages

    Returns:
        Decorator function
    """
    def decorator(func: F) -> F:
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            func_name = func.__name__
            logger.log(level, f"Entering {func_name}")
            try:
                result = func(*args, **kwargs)
                logger.log(level, f"Exiting {func_name}")
                return result
            except Exception as e:
                logger.log(level, f"Exiting {func_name} with error: {e}")
                raise
        return cast(F, wrapper)
    return decorator


def setup_exception_handler() -> None:
    """Set up global exception handler for better error reporting."""
    def handle_exception(
        exc_type: type[BaseException],
        exc_value: BaseException,
        exc_traceback: Optional[Any]
    ) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return

        logger = get_logger()
        logger.critical(
            "Uncaught exception",
            exc_info=(exc_type, exc_value, exc_traceback)
        )

    sys.excepthook = handle_exception
