"""
Common utilities and helpers for YTBot
"""

import re
import asyncio
import time
from typing import Callable, Any, Optional
from pathlib import Path

from ..core.logger import get_logger

logger = get_logger(__name__)


def retry(max_retries: int = 3, initial_delay: float = 1.0, backoff_factor: float = 2.0):
    """
    Decorator for retrying functions with exponential backoff

    Args:
        max_retries: Maximum number of retry attempts
        initial_delay: Initial delay between retries in seconds
        backoff_factor: Factor to multiply delay by for each retry
    """
    def decorator(func: Callable) -> Callable:
        async def async_wrapper(*args, **kwargs) -> Any:
            delay = initial_delay
            for attempt in range(max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    if attempt == max_retries:
                        logger.error(f"Function {func.__name__} failed after {max_retries} retries: {e}")
                        raise

                    logger.warning(f"Function {func.__name__} failed (attempt {attempt + 1}/{max_retries + 1}): {e}")
                    logger.info(f"Retrying in {delay:.2f} seconds...")
                    await asyncio.sleep(delay)
                    delay *= backoff_factor

            return None

        def sync_wrapper(*args, **kwargs) -> Any:
            delay = initial_delay
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    if attempt == max_retries:
                        logger.error(f"Function {func.__name__} failed after {max_retries} retries: {e}")
                        raise

                    logger.warning(f"Function {func.__name__} failed (attempt {attempt + 1}/{max_retries + 1}): {e}")
                    logger.info(f"Retrying in {delay:.2f} seconds...")
                    time.sleep(delay)
                    delay *= backoff_factor

            return None

        # Return appropriate wrapper based on whether func is async
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper

    return decorator


def format_file_size(size_bytes: int) -> str:
    """
    Format file size in human-readable format

    Args:
        size_bytes: File size in bytes

    Returns:
        str: Formatted file size (e.g., "1.5 MB")
    """
    if size_bytes == 0:
        return "0 B"

    units = ["B", "KB", "MB", "GB", "TB"]
    unit_index = 0
    size = float(size_bytes)

    while size >= 1024 and unit_index < len(units) - 1:
        size /= 1024
        unit_index += 1

    if unit_index == 0:
        return f"{int(size)} {units[unit_index]}"
    else:
        return f"{size:.1f} {units[unit_index]}"


def safe_truncate_filename(filename: str, max_bytes: int = 64) -> str:
    """
    Safely truncate filename to fit within byte limit while preserving extension

    Args:
        filename: Original filename
        max_bytes: Maximum bytes for filename

    Returns:
        str: Truncated filename
    """
    if len(filename.encode('utf-8')) <= max_bytes:
        return filename

    # Split filename and extension
    name, ext = Path(filename).stem, Path(filename).suffix

    # Calculate available space for name (reserve space for extension)
    ext_bytes = len(ext.encode('utf-8'))
    available_bytes = max_bytes - ext_bytes

    if available_bytes <= 0:
        # Extension alone is too long, truncate it
        return filename[:max_bytes]

    # Truncate name to fit
    truncated_name = name
    while len(truncated_name.encode('utf-8')) > available_bytes:
        truncated_name = truncated_name[:-1]
        if not truncated_name:
            break

    return f"{truncated_name}{ext}"


def sanitize_filename(filename: str) -> str:
    """
    Sanitize filename by removing invalid characters and limiting length

    Args:
        filename: Original filename

    Returns:
        str: Sanitized filename
    """
    # Remove or replace invalid characters
    sanitized = re.sub(r'[\\/:*?"<>|]', '_', filename)

    # Remove non-printable characters
    sanitized = ''.join(char for char in sanitized if ord(char) >= 32)

    # Limit filename length
    if len(sanitized) > 200:
        name, ext = Path(sanitized).stem, Path(sanitized).suffix
        sanitized = f"{name[:200 - len(ext)]}{ext}"

    return sanitized


def is_valid_url(url: str) -> bool:
    """
    Basic URL validation

    Args:
        url: URL to validate

    Returns:
        bool: True if URL appears valid
    """
    if not url or not isinstance(url, str):
        return False

    url = url.strip()
    return len(url) > 10 and (url.startswith('http://') or url.startswith('https://'))


def extract_domain(url: str) -> Optional[str]:
    """
    Extract domain from URL

    Args:
        url: URL to extract domain from

    Returns:
        str: Domain name or None if extraction fails
    """
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        return parsed.netloc.lower()
    except Exception:
        return None