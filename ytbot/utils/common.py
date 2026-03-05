"""
Common utilities for YTBot

Provides shared utility functions used across the application.
"""

import re
import os
import hashlib
import html
from typing import Optional, List, Dict, Any, Tuple, TypeVar, Union
from pathlib import Path
from datetime import datetime


# Type aliases
PathLike = Union[str, Path]
T = TypeVar('T')


def sanitize_filename(filename: str, max_length: int = 100) -> str:
    """
    Sanitize a filename by removing invalid characters.
    
    Args:
        filename: Original filename
        max_length: Maximum length for the filename
        
    Returns:
        Sanitized filename
    """
    # Remove invalid characters
    sanitized = re.sub(r'[<>:"/\\|?*]', '', filename)
    
    # Replace multiple spaces with single space
    sanitized = re.sub(r'\s+', ' ', sanitized)
    
    # Strip leading/trailing whitespace
    sanitized = sanitized.strip()
    
    # Limit length
    if len(sanitized) > max_length:
        name, ext = os.path.splitext(sanitized)
        sanitized = name[:max_length - len(ext)] + ext
    
    # Ensure not empty
    if not sanitized:
        sanitized = "unnamed"
    
    return sanitized


def format_duration(seconds: Optional[int]) -> str:
    """
    Format duration in seconds to human-readable string.
    
    Args:
        seconds: Duration in seconds
        
    Returns:
        Formatted duration string (e.g., "5:30" or "1:23:45")
    """
    if seconds is None:
        return "Unknown"
    
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    
    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    else:
        return f"{minutes}:{secs:02d}"


def format_file_size(size_bytes: Optional[int]) -> str:
    """
    Format file size in bytes to human-readable string.
    
    Args:
        size_bytes: Size in bytes
        
    Returns:
        Formatted size string (e.g., "1.5 MB")
    """
    if size_bytes is None:
        return "Unknown"
    
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    
    return f"{size_bytes:.1f} PB"


def truncate_text(text: str, max_length: int = 100, suffix: str = "...") -> str:
    """
    Truncate text to maximum length.
    
    Args:
        text: Original text
        max_length: Maximum length
        suffix: Suffix to add if truncated
        
    Returns:
        Truncated text
    """
    if len(text) <= max_length:
        return text
    
    return text[:max_length - len(suffix)] + suffix


def escape_markdown(text: str) -> str:
    """
    Escape markdown special characters in text.
    
    Args:
        text: Original text
        
    Returns:
        Escaped text
    """
    # Characters to escape in Markdown
    chars_to_escape = r'\*\_\[\]\(\)`>#\+\-=\{\}\.!'
    return re.sub(f'([{re.escape(chars_to_escape)}])', r'\\\1', text)


def escape_html_text(text: str) -> str:
    """
    Escape HTML special characters.
    
    Args:
        text: Original text
        
    Returns:
        Escaped text
    """
    return html.escape(text)


def generate_id(*args: Any) -> str:
    """
    Generate a unique ID from input arguments.
    
    Args:
        *args: Arguments to hash
        
    Returns:
        Unique ID string
    """
    content = "|".join(str(arg) for arg in args)
    return hashlib.md5(content.encode()).hexdigest()[:12]


def parse_url(url: str) -> Dict[str, Any]:
    """
    Parse URL and extract components.
    
    Args:
        url: URL to parse
        
    Returns:
        Dictionary with URL components
    """
    from urllib.parse import urlparse, parse_qs
    
    parsed = urlparse(url)
    query_params = parse_qs(parsed.query)
    
    # Flatten single-value lists
    query_params = {k: v[0] if len(v) == 1 else v for k, v in query_params.items()}
    
    return {
        "scheme": parsed.scheme,
        "netloc": parsed.netloc,
        "path": parsed.path,
        "params": parsed.params,
        "query": query_params,
        "fragment": parsed.fragment,
        "hostname": parsed.hostname,
        "port": parsed.port,
    }


def is_valid_url(url: str) -> bool:
    """
    Check if string is a valid URL.
    
    Args:
        url: URL to validate
        
    Returns:
        True if valid URL
    """
    if not url or not isinstance(url, str):
        return False
    
    url = url.strip()
    pattern = re.compile(
        r'^https?://'  # http:// or https://
        r'(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+[A-Z]{2,6}\.?|'  # domain
        r'localhost|'  # localhost
        r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})'  # or ip
        r'(?::\d+)?'  # optional port
        r'(?:/?|[/?]\S+)$',
        re.IGNORECASE
    )
    return bool(pattern.match(url))


def get_file_extension(filename: str) -> str:
    """
    Get file extension from filename.
    
    Args:
        filename: Filename
        
    Returns:
        File extension (lowercase, without dot)
    """
    _, ext = os.path.splitext(filename)
    return ext.lower().lstrip('.')


def ensure_directory(path: PathLike) -> Path:
    """
    Ensure directory exists, creating it if necessary.
    
    Args:
        path: Directory path
        
    Returns:
        Path object
    """
    path_obj = Path(path)
    path_obj.mkdir(parents=True, exist_ok=True)
    return path_obj


def safe_delete(path: PathLike) -> bool:
    """
    Safely delete a file or directory.
    
    Args:
        path: Path to delete
        
    Returns:
        True if deleted successfully
    """
    try:
        path_obj = Path(path)
        if path_obj.is_file():
            path_obj.unlink()
        elif path_obj.is_dir():
            import shutil
            shutil.rmtree(path_obj)
        return True
    except Exception:
        return False


def calculate_directory_size(path: PathLike) -> int:
    """
    Calculate total size of directory in bytes.
    
    Args:
        path: Directory path
        
    Returns:
        Total size in bytes
    """
    total_size = 0
    path_obj = Path(path)
    
    if path_obj.is_file():
        return path_obj.stat().st_size
    
    for dirpath, dirnames, filenames in os.walk(path_obj):
        for f in filenames:
            fp = Path(dirpath) / f
            if fp.is_file():
                total_size += fp.stat().st_size
    
    return total_size


def format_timestamp(timestamp: Optional[float] = None, fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    """
    Format timestamp to string.
    
    Args:
        timestamp: Unix timestamp (None for current time)
        fmt: Format string
        
    Returns:
        Formatted timestamp string
    """
    if timestamp is None:
        dt = datetime.now()
    else:
        dt = datetime.fromtimestamp(timestamp)
    
    return dt.strftime(fmt)


def parse_timestamp(timestamp_str: str, fmt: str = "%Y-%m-%d %H:%M:%S") -> Optional[datetime]:
    """
    Parse timestamp string to datetime object.
    
    Args:
        timestamp_str: Timestamp string
        fmt: Format string
        
    Returns:
        Datetime object or None if parsing fails
    """
    try:
        return datetime.strptime(timestamp_str, fmt)
    except ValueError:
        return None


def chunk_list(lst: List[T], chunk_size: int) -> List[List[T]]:
    """
    Split list into chunks of specified size.
    
    Args:
        lst: List to split
        chunk_size: Size of each chunk
        
    Returns:
        List of chunks
    """
    return [lst[i:i + chunk_size] for i in range(0, len(lst), chunk_size)]


def deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """
    Deep merge two dictionaries.
    
    Args:
        base: Base dictionary
        override: Dictionary with override values
        
    Returns:
        Merged dictionary
    """
    result = base.copy()
    
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    
    return result


def mask_sensitive_data(data: Dict[str, Any], sensitive_keys: Optional[List[str]] = None) -> Dict[str, Any]:
    """
    Mask sensitive data in dictionary.
    
    Args:
        data: Dictionary to mask
        sensitive_keys: Keys to mask (default: common sensitive keys)
        
    Returns:
        Masked dictionary
    """
    if sensitive_keys is None:
        sensitive_keys = ["password", "token", "secret", "key", "auth", "credential"]
    
    result = {}
    for key, value in data.items():
        key_lower = key.lower()
        is_sensitive = any(sk in key_lower for sk in sensitive_keys)
        
        if is_sensitive and isinstance(value, str):
            if len(value) > 8:
                result[key] = value[:4] + "****" + value[-4:]
            else:
                result[key] = "****"
        elif isinstance(value, dict):
            result[key] = mask_sensitive_data(value, sensitive_keys)
        else:
            result[key] = value
    
    return result
