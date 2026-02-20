"""
Enhanced logging configuration and utilities for YTBot with detailed diagnostics
"""

import logging
import logging.handlers
import os
import sys
import time
import traceback
from datetime import datetime
from typing import Optional, Dict, Any, Callable
from functools import wraps

from .config import CONFIG


class YTBotLogger:
    """Enhanced logger with detailed diagnostics and performance tracking"""

    def __init__(self, name: str = 'ytbot'):
        self.logger = logging.getLogger(name)
        self.logger.setLevel(getattr(logging, CONFIG['log']['level']))
        self.name = name
        self.start_times: Dict[str, float] = {}

        # Prevent duplicate handlers
        if not self.logger.handlers:
            self._setup_handlers()

    def _setup_handlers(self):
        """Set up console and file handlers with enhanced formatting"""
        detailed_format = (
            "%(asctime)s | %(levelname)s | %(name)s:%(lineno)d | %(message)s"
        )

        # Console handler with colors
        console_handler = ColoredConsoleHandler()
        console_handler.setLevel(getattr(logging, CONFIG['log']['level']))
        console_formatter = logging.Formatter(detailed_format)
        console_handler.setFormatter(console_formatter)
        self.logger.addHandler(console_handler)

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
            file_formatter = logging.Formatter(detailed_format)
            file_handler.setFormatter(file_formatter)
            self.logger.addHandler(file_handler)

        except Exception as e:
            self.logger.warning(f"Failed to set up file logging: {e}")

    def debug(self, msg: str, *args, **kwargs):
        """Debug level logging with extra context"""
        extra = kwargs.get('extra', {})
        extra.update(self._get_context_info())
        kwargs['extra'] = extra
        self.logger.debug(msg, *args, **kwargs)

    def info(self, msg: str, *args, **kwargs):
        """Info level logging with extra context"""
        extra = kwargs.get('extra', {})
        extra.update(self._get_context_info())
        kwargs['extra'] = extra
        self.logger.info(msg, *args, **kwargs)

    def warning(self, msg: str, *args, **kwargs):
        """Warning level logging with extra context"""
        extra = kwargs.get('extra', {})
        extra.update(self._get_context_info())
        kwargs['extra'] = extra
        self.logger.warning(msg, *args, **kwargs)

    def error(self, msg: str, *args, **kwargs):
        """Error level logging with extra context and stack trace"""
        extra = kwargs.get('extra', {})
        extra.update(self._get_context_info())
        if 'exc_info' not in kwargs:
            kwargs['exc_info'] = True
        kwargs['extra'] = extra
        self.logger.error(msg, *args, **kwargs)

    def critical(self, msg: str, *args, **kwargs):
        """Critical level logging with extra context and stack trace"""
        extra = kwargs.get('extra', {})
        extra.update(self._get_context_info())
        if 'exc_info' not in kwargs:
            kwargs['exc_info'] = True
        kwargs['extra'] = extra
        self.logger.critical(msg, *args, **kwargs)

    def exception(self, msg: str, *args, **kwargs):
        """Exception logging with full stack trace"""
        extra = kwargs.get('extra', {})
        extra.update(self._get_context_info())
        kwargs['extra'] = extra
        self.logger.exception(msg, *args, **kwargs)

    def _get_context_info(self) -> Dict[str, Any]:
        """Get additional context information for logging"""
        try:
            frame = sys._getframe(3)  # Get caller's frame
            context_module = frame.f_globals.get('__name__', 'unknown')
            context_function = frame.f_code.co_name
            context_line = frame.f_lineno
        except ValueError:
            context_module = 'unknown'
            context_function = 'unknown'
            context_line = 0

        return {
            'context_timestamp': datetime.now().isoformat(),
            'context_module': context_module,
            'context_function': context_function,
            'context_line': context_line,
            'context_pid': os.getpid(),
            'context_thread': threading.current_thread().name,
        }

    def start_timer(self, name: str):
        """Start a performance timer"""
        self.start_times[name] = time.time()
        self.debug(f"⏱️  Started timer: {name}")

    def end_timer(self, name: str) -> float:
        """End a performance timer and log the duration"""
        if name not in self.start_times:
            self.warning(f"⏱️  Timer not found: {name}")
            return 0.0

        duration = time.time() - self.start_times[name]
        self.info(f"⏱️  Timer {name} completed in {duration:.3f}s")
        del self.start_times[name]
        return duration

    def log_function_call(self, func_name: str, args: tuple = (), kwargs: dict = None):
        """Log function entry with arguments"""
        if kwargs is None:
            kwargs = {}

        args_str = ', '.join(repr(arg) for arg in args)
        kwargs_str = ', '.join(f"{k}={repr(v)}" for k, v in kwargs.items())

        if args_str and kwargs_str:
            params = f"{args_str}, {kwargs_str}"
        elif args_str:
            params = args_str
        elif kwargs_str:
            params = kwargs_str
        else:
            params = ""

        self.debug(f"🚀 Entering {func_name}({params})")

    def log_function_return(self, func_name: str, result: Any, duration: float = None):
        """Log function exit with result"""
        if duration:
            self.debug(f"✅ Exiting {func_name} -> {repr(result)} (took {duration:.3f}s)")
        else:
            self.debug(f"✅ Exiting {func_name} -> {repr(result)}")

    def log_download_progress(self, download_id: str, progress: float, speed: str = "", eta: str = ""):
        """Log download progress with formatted output"""
        progress_bar = self._create_progress_bar(progress)
        message = f"📥 [{download_id}] {progress_bar} {progress:.1f}%"

        if speed:
            message += f" | {speed}"
        if eta:
            message += f" | ETA: {eta}"

        self.info(message)

    def log_storage_operation(self, operation: str, file_path: str, storage_type: str, success: bool = True, error: str = ""):
        """Log storage operations"""
        status = "✅" if success else "❌"
        message = f"{status} Storage {operation}: {file_path} [{storage_type}]"

        if success:
            self.info(message)
        else:
            self.error(f"{message} - Error: {error}")

    def log_platform_detection(self, url: str, platform: str, success: bool = True):
        """Log platform detection results"""
        status = "✅" if success else "❌"
        message = f"{status} Platform detection: {platform} for {url}"

        if success:
            self.info(message)
        else:
            self.warning(message)

    def log_connection_status(self, service: str, status: str, details: str = ""):
        """Log connection status changes"""
        status_emoji = {
            "connected": "🟢",
            "disconnected": "🔴",
            "error": "❌",
            "warning": "🟡",
            "unknown": "⚪"
        }

        emoji = status_emoji.get(status.lower(), "⚪")
        message = f"{emoji} {service} connection: {status}"

        if details:
            message += f" ({details})"

        if status.lower() == "error":
            self.error(message)
        elif status.lower() == "warning":
            self.warning(message)
        else:
            self.info(message)

    def log_system_health(self, health_status: Dict[str, Any]):
        """Log system health status"""
        status = health_status.get('status', 'unknown')

        status_emoji = {
            'healthy': '🟢',
            'warning': '🟡',
            'critical': '🔴'
        }

        emoji = status_emoji.get(status, '⚪')
        message = f"{emoji} System health: {status}"

        # Add key metrics
        metrics = []
        if 'cpu_percent' in health_status:
            metrics.append(f"CPU: {health_status['cpu_percent']:.1f}%")
        if 'memory_percent' in health_status:
            metrics.append(f"Memory: {health_status['memory_percent']:.1f}%")
        if 'disk_usage' in health_status:
            metrics.append(f"Disk: {health_status['disk_usage']:.1f}%")

        if metrics:
            message += f" | {' | '.join(metrics)}"

        if status == 'critical':
            self.critical(message)
        elif status == 'warning':
            self.warning(message)
        else:
            self.info(message)

    def _create_progress_bar(self, progress: float, width: int = 20) -> str:
        """Create a text progress bar"""
        filled = int(width * progress / 100)
        bar = "█" * filled + "░" * (width - filled)
        return f"[{bar}]"


class ColoredConsoleHandler(logging.StreamHandler):
    """Console handler with colored output for better readability"""

    COLORS = {
        'DEBUG': '\033[36m',     # Cyan
        'INFO': '\033[32m',      # Green
        'WARNING': '\033[33m',   # Yellow
        'ERROR': '\033[31m',     # Red
        'CRITICAL': '\033[35m',  # Magenta
        'RESET': '\033[0m'       # Reset
    }

    def emit(self, record):
        """Emit a log record with color formatting"""
        # Add color to the levelname
        if hasattr(record, 'levelname'):
            color = self.COLORS.get(record.levelname, self.COLORS['RESET'])
            reset = self.COLORS['RESET']
            record.levelname = f"{color}{record.levelname}{reset}"

        super().emit(record)


def get_logger(name: Optional[str] = None) -> YTBotLogger:
    """Get an enhanced logger instance"""
    if name is None:
        # Get caller's module name
        import inspect
        frame = inspect.currentframe()
        if frame and frame.f_back:
            name = frame.f_back.f_globals.get('__name__', 'ytbot')
        else:
            name = 'ytbot'

    return YTBotLogger(name)


def setup_logger(name: str = 'ytbot') -> YTBotLogger:
    """Set up and return an enhanced logger"""
    return get_logger(name)


def setup_exception_handler():
    """Set up global exception handler with detailed logging"""
    def handle_exception(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return

        logger = get_logger()

        # Log the exception with full context
        logger.critical("🚨 Uncaught exception occurred")
        logger.critical(f"Exception type: {exc_type.__name__}")
        logger.critical(f"Exception message: {str(exc_value)}")

        # Log the full stack trace
        tb_lines = traceback.format_exception(exc_type, exc_value, exc_traceback)
        for line in tb_lines:
            logger.critical(f"📍 {line.strip()}")

        # Log system information
        logger.critical(f"💻 System info: Python {sys.version}")
        logger.critical(f"📁 Working directory: {os.getcwd()}")
        logger.critical(f"🕐 Timestamp: {datetime.now().isoformat()}")

    sys.excepthook = handle_exception


def log_function_entry_exit(logger: YTBotLogger = None):
    """Decorator to log function entry and exit with timing"""
    if logger is None:
        logger = get_logger()

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            func_name = f"{func.__module__}.{func.__name__}"

            # Log function entry
            logger.log_function_call(func_name, args, kwargs)

            # Start timer
            start_time = time.time()

            try:
                # Execute function
                result = func(*args, **kwargs)

                # Calculate duration
                duration = time.time() - start_time

                # Log successful exit
                logger.log_function_return(func_name, result, duration)

                return result

            except Exception as e:
                # Calculate duration
                duration = time.time() - start_time

                # Log error exit
                logger.error(f"❌ Function {func_name} failed after {duration:.3f}s: {str(e)}")
                raise

        return wrapper

    return decorator


# Import threading at the end to avoid circular imports
import threading
