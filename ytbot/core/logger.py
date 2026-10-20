"""
Logging configuration and utilities for YTBot.

Thin re-export layer over core.enhanced_logger so that all existing
``from ..core.logger import get_logger`` imports stay valid while
pointing at the unified enhanced implementation.
"""

from .enhanced_logger import (
    get_logger,
    setup_logger,
    setup_exception_handler,
    log_function_entry_exit,
)

__all__ = [
    'get_logger',
    'setup_logger',
    'setup_exception_handler',
    'log_function_entry_exit',
]
