"""
YTBot Terminal UI Module

Provides rich-based terminal interface for local interaction.

This module contains all components needed for the terminal user interface,
including the main TerminalUI class, output formatters, command registry,
and custom Rich widgets for rendering status bars, input prompts, and
main content areas.

Components:
    TerminalUI: Main terminal interface controller with layout management
    OutputFormatter: Static utility class for formatting various output types
    CommandRegistry: Registry and handler for terminal commands

Example:
    >>> from ytbot.ui import TerminalUI, OutputFormatter, CommandRegistry
    >>>
    >>> # Format a log message
    >>> message = OutputFormatter.format_log_message("INFO", "System started")
    >>> print(message)
    >>>
    >>> # Format download progress
    >>> progress = OutputFormatter.format_download_progress({
    ...     "progress": 78.5,
    ...     "speed": "2.5MB/s",
    ...     "eta": "00:01:30"
    ... })
"""

from .terminal import TerminalUI
from .formatter import OutputFormatter
from .commands import CommandRegistry

__all__ = [
    'TerminalUI',
    'OutputFormatter',
    'CommandRegistry',
]
