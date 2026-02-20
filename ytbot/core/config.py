"""
Core configuration management for YTBot

This module handles all configuration loading and validation for the bot.
Supports environment variables with sensible defaults.
"""

import os
from typing import List
from dotenv import load_dotenv

# Load environment variables
load_dotenv()


def get_env_or_default(env_name: str, default: str) -> str:
    """Get environment variable value or return default."""
    value = os.environ.get(env_name)
    return value if value is not None else default


def get_env_bool_or_default(env_name: str, default: bool) -> bool:
    """Get environment variable boolean value or return default."""
    value = os.environ.get(env_name)
    if value is None:
        return default
    value = value.lower()
    if value in ['true', 'yes', '1', 't', 'y']:
        return True
    elif value in ['false', 'no', '0', 'f', 'n']:
        return False
    return default


def get_env_int_or_default(env_name: str, default: int) -> int:
    """Get environment variable integer value or return default."""
    value = os.environ.get(env_name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def get_env_float_or_default(env_name: str, default: float) -> float:
    """Get environment variable float value or return default."""
    value = os.environ.get(env_name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


# Telegram Configuration
TELEGRAM_BOT_TOKEN = get_env_or_default("TELEGRAM_BOT_TOKEN", "")
ADMIN_CHAT_ID = get_env_or_default("ADMIN_CHAT_ID", "")
ALLOWED_CHAT_IDS = [ADMIN_CHAT_ID] if ADMIN_CHAT_ID else []

# Nextcloud Configuration
NEXTCLOUD_URL = get_env_or_default("NEXTCLOUD_URL", "")
NEXTCLOUD_USERNAME = get_env_or_default("NEXTCLOUD_USERNAME", "")
NEXTCLOUD_PASSWORD = get_env_or_default("NEXTCLOUD_PASSWORD", "")
NEXTCLOUD_UPLOAD_DIR = get_env_or_default("NEXTCLOUD_UPLOAD_DIR", "/YTBot")
NEXTCLOUD_CHUNK_SIZE = get_env_int_or_default("NEXTCLOUD_CHUNK_SIZE", 1024 * 1024 * 8)
NEXTCLOUD_TIMEOUT = get_env_int_or_default("NEXTCLOUD_TIMEOUT", 600)

# Local Storage Configuration
LOCAL_STORAGE_PATH = get_env_or_default("LOCAL_STORAGE_PATH", "./downloads")
LOCAL_STORAGE_ENABLED = get_env_bool_or_default("LOCAL_STORAGE_ENABLED", True)
LOCAL_STORAGE_MAX_SIZE_MB = get_env_int_or_default("LOCAL_STORAGE_MAX_SIZE_MB", 10240)
LOCAL_STORAGE_CLEANUP_AFTER_DAYS = get_env_int_or_default("LOCAL_STORAGE_CLEANUP_AFTER_DAYS", 7)

# System Configuration
MAX_CONCURRENT_DOWNLOADS = get_env_int_or_default("MAX_CONCURRENT_DOWNLOADS", 5)
LOG_LEVEL = get_env_or_default("LOG_LEVEL", "INFO")
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
LOG_FILE = get_env_or_default("LOG_FILE", "ytbot.log")
LOG_MAX_BYTES = get_env_int_or_default("LOG_MAX_BYTES", 10485760)
LOG_BACKUP_COUNT = get_env_int_or_default("LOG_BACKUP_COUNT", 5)

# Download Configuration
DOWNLOAD_TIMEOUT = get_env_int_or_default("DOWNLOAD_TIMEOUT", 3600)
MAX_RETRY_COUNT = get_env_int_or_default("MAX_RETRY_COUNT", 3)
INITIAL_RETRY_DELAY = get_env_float_or_default("INITIAL_RETRY_DELAY", 1.0)
CHECK_YT_DLP_VERSION = get_env_bool_or_default("CHECK_YT_DLP_VERSION", True)
VERSION_CHECK_TIMEOUT = get_env_int_or_default("VERSION_CHECK_TIMEOUT", 10)

# Progress Configuration
PROGRESS_UPDATE_INTERVAL = get_env_int_or_default("PROGRESS_UPDATE_INTERVAL", 10)

# Monitoring Configuration
MONITOR_INTERVAL = get_env_int_or_default("MONITOR_INTERVAL", 3600)
MIN_DISK_SPACE = get_env_float_or_default("MIN_DISK_SPACE", 1024.0)
MAX_CPU_LOAD = get_env_float_or_default("MAX_CPU_LOAD", 0.8)
MEMORY_THRESHOLD = get_env_int_or_default("MEMORY_THRESHOLD", 512)
USER_STATE_TIMEOUT = get_env_int_or_default("USER_STATE_TIMEOUT", 300)
CACHE_RETRY_INTERVAL = get_env_int_or_default("CACHE_RETRY_INTERVAL", 300)

# Security Configuration
CHECK_CERTIFICATE = get_env_bool_or_default("CHECK_CERTIFICATE", False)


# Main Configuration Dictionary
CONFIG = {
    "telegram": {
        "token": TELEGRAM_BOT_TOKEN,
        "allowed_chat_ids": ALLOWED_CHAT_IDS,
        "admin_chat_id": ADMIN_CHAT_ID,
    },
    "nextcloud": {
        "url": NEXTCLOUD_URL,
        "username": NEXTCLOUD_USERNAME,
        "password": NEXTCLOUD_PASSWORD,
        "upload_dir": NEXTCLOUD_UPLOAD_DIR,
        "chunk_size": NEXTCLOUD_CHUNK_SIZE,
        "timeout": NEXTCLOUD_TIMEOUT,
        "connection_retries": get_env_int_or_default("NEXTCLOUD_CONNECTION_RETRIES", 3),
        "connection_retry_delay": get_env_float_or_default("NEXTCLOUD_CONNECTION_RETRY_DELAY", 5.0),
        "upload_retries": get_env_int_or_default("NEXTCLOUD_UPLOAD_RETRIES", 3),
        "upload_retry_delay": get_env_float_or_default("NEXTCLOUD_UPLOAD_RETRY_DELAY", 5.0),
        "upload_timeout": get_env_int_or_default("NEXTCLOUD_UPLOAD_TIMEOUT", 600),
        "verify_file_size": get_env_bool_or_default("NEXTCLOUD_VERIFY_FILE_SIZE", True),
    },
    "local_storage": {
        "path": LOCAL_STORAGE_PATH,
        "enabled": LOCAL_STORAGE_ENABLED,
        "max_size_mb": LOCAL_STORAGE_MAX_SIZE_MB,
        "cleanup_after_days": LOCAL_STORAGE_CLEANUP_AFTER_DAYS,
    },
    "download": {
        "timeout": DOWNLOAD_TIMEOUT,
        "max_retry_count": MAX_RETRY_COUNT,
        "initial_retry_delay": INITIAL_RETRY_DELAY,
        "check_yt_dlp_version": CHECK_YT_DLP_VERSION,
        "version_check_timeout": VERSION_CHECK_TIMEOUT,
        "quiet": get_env_bool_or_default("DOWNLOAD_QUIET", False),
        "no_warnings": get_env_bool_or_default("DOWNLOAD_NO_WARNINGS", False),
        "retries": get_env_int_or_default("DOWNLOAD_RETRIES", 3),
        "fragment_retries": get_env_int_or_default("DOWNLOAD_FRAGMENT_RETRIES", 10),
        "ignore_errors": get_env_bool_or_default("DOWNLOAD_IGNORE_ERRORS", True),
        "user_agent": get_env_or_default(
            "DOWNLOAD_USER_AGENT",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
        ),
        "http_headers": {
            "User-Agent": get_env_or_default(
                "DOWNLOAD_USER_AGENT",
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            )
        },
        "audio_format": get_env_or_default("AUDIO_FORMAT", "bestaudio/best"),
        "audio_codec": get_env_or_default("AUDIO_CODEC", "mp3"),
        "audio_quality": get_env_int_or_default("AUDIO_QUALITY", 192),
        "video_format": get_env_or_default(
            "VIDEO_FORMAT",
            "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        ),
        "video_output_format": get_env_or_default("VIDEO_OUTPUT_FORMAT", "mp4"),
        "prefer_ffmpeg": True,
        "ignore_no_formats_error": True,
        "allow_playlist_files": True,
        "merge_output_format": "mp4",
        "sleep_interval_requests": get_env_float_or_default("SLEEP_INTERVAL_REQUESTS", 0.5),
        "sleep_interval": get_env_float_or_default("SLEEP_INTERVAL", 2),
        "max_sleep_interval": get_env_float_or_default("MAX_SLEEP_INTERVAL", 10),
        "socket_timeout": get_env_int_or_default("DOWNLOAD_SOCKET_TIMEOUT", 20),
        "progress_update_interval": PROGRESS_UPDATE_INTERVAL,
    },
    "log": {
        "level": LOG_LEVEL,
        "format": LOG_FORMAT,
        "file": LOG_FILE,
        "max_bytes": LOG_MAX_BYTES,
        "backup_count": LOG_BACKUP_COUNT,
    },
    "app": {
        "name": "YouTube Download Bot",
        "version": "2.0.0",
        "max_message_length": 4096,
        "max_concurrent_downloads": MAX_CONCURRENT_DOWNLOADS,
    },
    "monitor": {
        "interval": MONITOR_INTERVAL,
        "min_disk_space": MIN_DISK_SPACE,
        "max_cpu_load": MAX_CPU_LOAD,
        "memory_threshold": MEMORY_THRESHOLD,
        "user_state_timeout": USER_STATE_TIMEOUT,
        "cache_retry_interval": CACHE_RETRY_INTERVAL,
    },
    "security": {
        "check_certificate": CHECK_CERTIFICATE,
    },
}


def validate_config() -> List[str]:
    """
    Validate configuration and return list of missing required configurations.

    Returns:
        List[str]: List of missing configuration errors
    """
    errors = []

    # Validate Telegram configuration
    if not CONFIG["telegram"]["token"]:
        errors.append("TELEGRAM_BOT_TOKEN is required")

    # Validate Nextcloud configuration (optional but recommended)
    if not CONFIG["nextcloud"]["url"]:
        errors.append("NEXTCLOUD_URL is recommended for cloud storage")
    if not CONFIG["nextcloud"]["username"]:
        errors.append("NEXTCLOUD_USERNAME is recommended for cloud storage")
    if not CONFIG["nextcloud"]["password"]:
        errors.append("NEXTCLOUD_PASSWORD is recommended for cloud storage")

    # Validate allowed users
    if not CONFIG["telegram"]["allowed_chat_ids"]:
        errors.append("No allowed chat IDs configured. Please set ADMIN_CHAT_ID")

    return errors