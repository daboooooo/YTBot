"""
Core configuration management for YTBot

This module handles all configuration loading and validation for the bot.
Supports environment variables with sensible defaults using type-safe dataclasses.
"""

import os
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Callable, TypeVar, Type
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

T = TypeVar('T')


class ConfigError(Exception):
    """Base exception for configuration errors"""
    pass


class ConfigValidationError(ConfigError):
    """Raised when configuration validation fails"""
    pass


class ConfigTypeError(ConfigError):
    """Raised when configuration type conversion fails"""
    pass


def get_env_or_default(env_name: str, default: T, converter: Optional[Callable[[str], T]] = None) -> T:
    """
    Get environment variable value or return default.
    
    Args:
        env_name: Environment variable name
        default: Default value if variable not set
        converter: Optional function to convert the value
        
    Returns:
        The environment variable value or default
    """
    value = os.environ.get(env_name)
    if value is None:
        return default
    if converter is not None:
        try:
            return converter(value)
        except (ValueError, TypeError) as e:
            raise ConfigTypeError(f"Failed to convert {env_name}={value}: {e}")
    return value  # type: ignore


def get_env_bool(env_name: str, default: bool = False) -> bool:
    """Get environment variable as boolean."""
    def converter(v: str) -> bool:
        v_lower = v.lower().strip()
        if v_lower in ('true', 'yes', '1', 't', 'y', 'on'):
            return True
        if v_lower in ('false', 'no', '0', 'f', 'n', 'off'):
            return False
        raise ValueError(f"Cannot convert '{v}' to boolean")
    return get_env_or_default(env_name, default, converter)


def get_env_int(env_name: str, default: int = 0, min_value: Optional[int] = None, max_value: Optional[int] = None) -> int:
    """Get environment variable as integer with optional range validation."""
    def converter(v: str) -> int:
        result = int(v)
        if min_value is not None and result < min_value:
            raise ValueError(f"Value {result} is below minimum {min_value}")
        if max_value is not None and result > max_value:
            raise ValueError(f"Value {result} is above maximum {max_value}")
        return result
    return get_env_or_default(env_name, default, converter)


def get_env_float(env_name: str, default: float = 0.0, min_value: Optional[float] = None, max_value: Optional[float] = None) -> float:
    """Get environment variable as float with optional range validation."""
    def converter(v: str) -> float:
        result = float(v)
        if min_value is not None and result < min_value:
            raise ValueError(f"Value {result} is below minimum {min_value}")
        if max_value is not None and result > max_value:
            raise ValueError(f"Value {result} is above maximum {max_value}")
        return result
    return get_env_or_default(env_name, default, converter)


def get_env_str(env_name: str, default: str = "", allowed_values: Optional[List[str]] = None) -> str:
    """Get environment variable as string with optional allowed values validation."""
    def converter(v: str) -> str:
        if allowed_values is not None and v not in allowed_values:
            raise ValueError(f"Value '{v}' not in allowed values: {allowed_values}")
        return v
    return get_env_or_default(env_name, default, converter)


def get_env_list(env_name: str, default: Optional[List[str]] = None, separator: str = ",") -> List[str]:
    """Get environment variable as list of strings."""
    def converter(v: str) -> List[str]:
        if not v.strip():
            return []
        return [item.strip() for item in v.split(separator) if item.strip()]
    return get_env_or_default(env_name, default or [], converter)


@dataclass(frozen=True)
class TelegramConfig:
    """Telegram Bot configuration"""
    token: str = field(default_factory=lambda: get_env_str("TELEGRAM_BOT_TOKEN"))
    admin_chat_id: str = field(default_factory=lambda: get_env_str("ADMIN_CHAT_ID"))
    
    @property
    def allowed_chat_ids(self) -> List[str]:
        """Get list of allowed chat IDs"""
        if self.admin_chat_id:
            return [self.admin_chat_id]
        return []


@dataclass(frozen=True)
class NextcloudConfig:
    """Nextcloud storage configuration"""
    url: str = field(default_factory=lambda: get_env_str("NEXTCLOUD_URL"))
    username: str = field(default_factory=lambda: get_env_str("NEXTCLOUD_USERNAME"))
    password: str = field(default_factory=lambda: get_env_str("NEXTCLOUD_PASSWORD"))
    upload_dir: str = field(default_factory=lambda: get_env_str("NEXTCLOUD_UPLOAD_DIR", "/YTBot"))
    chunk_size: int = field(default_factory=lambda: get_env_int("NEXTCLOUD_CHUNK_SIZE", 8 * 1024 * 1024))
    timeout: int = field(default_factory=lambda: get_env_int("NEXTCLOUD_TIMEOUT", 600, min_value=1))
    connection_retries: int = field(default_factory=lambda: get_env_int("NEXTCLOUD_CONNECTION_RETRIES", 3, min_value=0))
    connection_retry_delay: float = field(default_factory=lambda: get_env_float("NEXTCLOUD_CONNECTION_RETRY_DELAY", 5.0, min_value=0))
    upload_retries: int = field(default_factory=lambda: get_env_int("NEXTCLOUD_UPLOAD_RETRIES", 3, min_value=0))
    upload_retry_delay: float = field(default_factory=lambda: get_env_float("NEXTCLOUD_UPLOAD_RETRY_DELAY", 5.0, min_value=0))
    upload_timeout: int = field(default_factory=lambda: get_env_int("NEXTCLOUD_UPLOAD_TIMEOUT", 600, min_value=1))
    verify_file_size: bool = field(default_factory=lambda: get_env_bool("NEXTCLOUD_VERIFY_FILE_SIZE", True))


@dataclass(frozen=True)
class LocalStorageConfig:
    """Local storage configuration"""
    path: str = field(default_factory=lambda: get_env_str("LOCAL_STORAGE_PATH", "./downloads"))
    enabled: bool = field(default_factory=lambda: get_env_bool("LOCAL_STORAGE_ENABLED", True))
    max_size_mb: int = field(default_factory=lambda: get_env_int("LOCAL_STORAGE_MAX_SIZE_MB", 10240, min_value=0))
    cleanup_after_days: int = field(default_factory=lambda: get_env_int("LOCAL_STORAGE_CLEANUP_AFTER_DAYS", 7, min_value=0))
    delete_after_upload: bool = field(default_factory=lambda: get_env_bool("LOCAL_STORAGE_DELETE_AFTER_UPLOAD", False))


@dataclass(frozen=True)
class DownloadConfig:
    """Download configuration"""
    timeout: int = field(default_factory=lambda: get_env_int("DOWNLOAD_TIMEOUT", 3600, min_value=1))
    max_retry_count: int = field(default_factory=lambda: get_env_int("MAX_RETRY_COUNT", 3, min_value=0))
    initial_retry_delay: float = field(default_factory=lambda: get_env_float("INITIAL_RETRY_DELAY", 1.0, min_value=0))
    check_yt_dlp_version: bool = field(default_factory=lambda: get_env_bool("CHECK_YT_DLP_VERSION", True))
    version_check_timeout: int = field(default_factory=lambda: get_env_int("VERSION_CHECK_TIMEOUT", 10, min_value=1))
    quiet: bool = field(default_factory=lambda: get_env_bool("DOWNLOAD_QUIET", False))
    no_warnings: bool = field(default_factory=lambda: get_env_bool("DOWNLOAD_NO_WARNINGS", False))
    retries: int = field(default_factory=lambda: get_env_int("DOWNLOAD_RETRIES", 3, min_value=0))
    fragment_retries: int = field(default_factory=lambda: get_env_int("DOWNLOAD_FRAGMENT_RETRIES", 10, min_value=0))
    ignore_errors: bool = field(default_factory=lambda: get_env_bool("DOWNLOAD_IGNORE_ERRORS", True))
    user_agent: str = field(default_factory=lambda: get_env_str(
        "DOWNLOAD_USER_AGENT",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    ))
    audio_format: str = field(default_factory=lambda: get_env_str("AUDIO_FORMAT", "bestaudio/best"))
    audio_codec: str = field(default_factory=lambda: get_env_str("AUDIO_CODEC", "mp3"))
    audio_quality: int = field(default_factory=lambda: get_env_int("AUDIO_QUALITY", 192, min_value=0))
    video_format: str = field(default_factory=lambda: get_env_str(
        "VIDEO_FORMAT",
        "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
    ))
    video_output_format: str = field(default_factory=lambda: get_env_str("VIDEO_OUTPUT_FORMAT", "mp4"))
    merge_output_format: str = field(default_factory=lambda: get_env_str("MERGE_OUTPUT_FORMAT", "mp4"))
    prefer_ffmpeg: bool = field(default_factory=lambda: get_env_bool("PREFER_FFMPEG", True))
    ignore_no_formats_error: bool = field(default_factory=lambda: get_env_bool("IGNORE_NO_FORMATS_ERROR", True))
    allow_playlist_files: bool = field(default_factory=lambda: get_env_bool("ALLOW_PLAYLIST_FILES", True))
    sleep_interval_requests: float = field(default_factory=lambda: get_env_float("SLEEP_INTERVAL_REQUESTS", 0.5, min_value=0))
    sleep_interval: float = field(default_factory=lambda: get_env_float("SLEEP_INTERVAL", 2.0, min_value=0))
    max_sleep_interval: float = field(default_factory=lambda: get_env_float("MAX_SLEEP_INTERVAL", 10.0, min_value=0))
    socket_timeout: int = field(default_factory=lambda: get_env_int("DOWNLOAD_SOCKET_TIMEOUT", 20, min_value=1))
    progress_update_interval: int = field(default_factory=lambda: get_env_int("PROGRESS_UPDATE_INTERVAL", 10, min_value=1))
    
    @property
    def http_headers(self) -> Dict[str, str]:
        """Get HTTP headers for downloads"""
        return {"User-Agent": self.user_agent}


@dataclass(frozen=True)
class LogConfig:
    """Logging configuration"""
    level: str = field(default_factory=lambda: get_env_str("LOG_LEVEL", "INFO", 
                                                           allowed_values=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]))
    format: str = field(default_factory=lambda: get_env_str(
        "LOG_FORMAT",
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    ))
    file: str = field(default_factory=lambda: get_env_str("LOG_FILE", "ytbot.log"))
    max_bytes: int = field(default_factory=lambda: get_env_int("LOG_MAX_BYTES", 10 * 1024 * 1024, min_value=1024))
    backup_count: int = field(default_factory=lambda: get_env_int("LOG_BACKUP_COUNT", 5, min_value=0))


@dataclass(frozen=True)
class AppConfig:
    """Application configuration"""
    name: str = "YouTube Download Bot"
    version: str = "2.0.0"
    max_message_length: int = 4096
    max_concurrent_downloads: int = field(default_factory=lambda: get_env_int("MAX_CONCURRENT_DOWNLOADS", 5, min_value=1))


@dataclass(frozen=True)
class MonitorConfig:
    """Monitoring configuration"""
    interval: int = field(default_factory=lambda: get_env_int("MONITOR_INTERVAL", 3600, min_value=1))
    min_disk_space: float = field(default_factory=lambda: get_env_float("MIN_DISK_SPACE", 1024.0, min_value=0))
    max_cpu_load: float = field(default_factory=lambda: get_env_float("MAX_CPU_LOAD", 0.8, min_value=0.0, max_value=1.0))
    memory_threshold: int = field(default_factory=lambda: get_env_int("MEMORY_THRESHOLD", 512, min_value=0))
    user_state_timeout: int = field(default_factory=lambda: get_env_int("USER_STATE_TIMEOUT", 300, min_value=1))
    cache_retry_interval: int = field(default_factory=lambda: get_env_int("CACHE_RETRY_INTERVAL", 300, min_value=1))


@dataclass(frozen=True)
class SecurityConfig:
    """Security configuration"""
    check_certificate: bool = field(default_factory=lambda: get_env_bool("CHECK_CERTIFICATE", False))


@dataclass(frozen=True)
class TwitterConfig:
    """Twitter/X configuration"""
    cookies_file: str = field(default_factory=lambda: get_env_str("TWITTER_COOKIES_FILE"))
    cookies_json: str = field(default_factory=lambda: get_env_str("TWITTER_COOKIES_JSON"))


@dataclass(frozen=True)
class YouTubeConfig:
    """YouTube configuration"""
    cookies_file: str = field(default_factory=lambda: get_env_str("YOUTUBE_COOKIES_FILE"))
    cookies_json: str = field(default_factory=lambda: get_env_str("YOUTUBE_COOKIES_JSON"))


@dataclass(frozen=True)
class BotConfig:
    """Main bot configuration container"""
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    nextcloud: NextcloudConfig = field(default_factory=NextcloudConfig)
    local_storage: LocalStorageConfig = field(default_factory=LocalStorageConfig)
    download: DownloadConfig = field(default_factory=DownloadConfig)
    log: LogConfig = field(default_factory=LogConfig)
    app: AppConfig = field(default_factory=AppConfig)
    monitor: MonitorConfig = field(default_factory=MonitorConfig)
    security: SecurityConfig = field(default_factory=SecurityConfig)
    twitter: TwitterConfig = field(default_factory=TwitterConfig)
    youtube: YouTubeConfig = field(default_factory=YouTubeConfig)
    
    def validate(self) -> List[str]:
        """
        Validate configuration and return list of errors.
        
        Returns:
            List of validation error messages
        """
        errors = []
        
        # Validate Telegram token
        if not self.telegram.token:
            errors.append("TELEGRAM_BOT_TOKEN is required")
        
        # Validate admin chat ID
        if not self.telegram.admin_chat_id:
            errors.append("ADMIN_CHAT_ID is required")
        
        # Validate Nextcloud configuration (optional but recommended)
        if self.nextcloud.url:
            if not self.nextcloud.username:
                errors.append("NEXTCLOUD_USERNAME is recommended when NEXTCLOUD_URL is set")
            if not self.nextcloud.password:
                errors.append("NEXTCLOUD_PASSWORD is recommended when NEXTCLOUD_URL is set")
        
        return errors
    
    def validate_or_raise(self) -> None:
        """
        Validate configuration and raise exception if invalid.
        
        Raises:
            ConfigValidationError: If validation fails
        """
        errors = self.validate()
        if errors:
            raise ConfigValidationError(f"Configuration validation failed: {'; '.join(errors)}")
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert configuration to dictionary"""
        result = {}
        for field_name in self.__dataclass_fields__:
            value = getattr(self, field_name)
            if hasattr(value, '__dataclass_fields__'):
                result[field_name] = {}
                for sub_field_name in value.__dataclass_fields__:
                    sub_value = getattr(value, sub_field_name)
                    # Skip properties
                    if not callable(sub_value):
                        result[field_name][sub_field_name] = sub_value
            else:
                result[field_name] = value
        return result


# Global configuration instance
_CONFIG: Optional[BotConfig] = None


def get_config() -> BotConfig:
    """
    Get the global configuration instance.
    
    Returns:
        BotConfig instance
    """
    global _CONFIG
    if _CONFIG is None:
        _CONFIG = BotConfig()
    return _CONFIG


def reload_config() -> BotConfig:
    """
    Reload configuration from environment variables.
    
    Returns:
        New BotConfig instance
    """
    global _CONFIG
    _CONFIG = BotConfig()
    return _CONFIG


# Backward compatibility - maintain old CONFIG dict access
class ConfigDictWrapper:
    """Wrapper to provide dict-like access to config for backward compatibility"""
    
    def __init__(self, config: BotConfig):
        self._config = config
    
    def __getitem__(self, key: str) -> Any:
        """Get configuration section by key"""
        if hasattr(self._config, key):
            section = getattr(self._config, key)
            if hasattr(section, '__dataclass_fields__'):
                # Convert dataclass to dict-like access
                return ConfigSectionWrapper(section)
            return section
        raise KeyError(key)
    
    def get(self, key: str, default: Any = None) -> Any:
        """Get configuration section with default"""
        try:
            return self[key]
        except KeyError:
            return default
    
    def __contains__(self, key: str) -> bool:
        """Check if key exists"""
        return hasattr(self._config, key)


class ConfigSectionWrapper:
    """Wrapper for config sections to provide dict-like access"""
    
    def __init__(self, section: Any):
        self._section = section
    
    def __getitem__(self, key: str) -> Any:
        """Get configuration value by key"""
        if hasattr(self._section, key):
            return getattr(self._section, key)
        raise KeyError(key)
    
    def get(self, key: str, default: Any = None) -> Any:
        """Get configuration value with default"""
        try:
            return self[key]
        except KeyError:
            return default
    
    def __contains__(self, key: str) -> bool:
        """Check if key exists"""
        return hasattr(self._section, key)


# Create backward-compatible CONFIG
CONFIG = ConfigDictWrapper(get_config())


# Backward-compatible validation function
def validate_config() -> List[str]:
    """
    Validate configuration and return list of missing required configurations.
    
    Returns:
        List[str]: List of missing configuration errors
    """
    return get_config().validate()
