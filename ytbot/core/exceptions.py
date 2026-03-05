"""
Custom exceptions for YTBot

Provides a hierarchy of exceptions for different error scenarios
to enable precise error handling and user-friendly error messages.
"""

from typing import Optional, Dict, Any


class YTBotError(Exception):
    """Base exception for all YTBot errors"""
    
    def __init__(
        self,
        message: str,
        error_code: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        cause: Optional[Exception] = None
    ):
        super().__init__(message)
        self.message = message
        self.error_code = error_code or self._get_default_error_code()
        self.details = details or {}
        self.cause = cause
    
    def _get_default_error_code(self) -> str:
        """Get default error code based on class name"""
        return self.__class__.__name__.upper().replace("ERROR", "")
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert exception to dictionary for serialization"""
        result = {
            "error_code": self.error_code,
            "message": self.message,
            "details": self.details
        }
        if self.cause:
            result["cause"] = str(self.cause)
        return result
    
    def __str__(self) -> str:
        if self.error_code:
            return f"[{self.error_code}] {self.message}"
        return self.message


# Configuration Errors
class ConfigError(YTBotError):
    """Base exception for configuration errors"""
    pass


class ConfigValidationError(ConfigError):
    """Raised when configuration validation fails"""
    pass


class ConfigTypeError(ConfigError):
    """Raised when configuration type conversion fails"""
    pass


class ConfigMissingError(ConfigError):
    """Raised when required configuration is missing"""
    pass


# Platform Errors
class PlatformError(YTBotError):
    """Base exception for platform-related errors"""
    
    def __init__(
        self,
        message: str,
        platform: Optional[str] = None,
        url: Optional[str] = None,
        **kwargs
    ):
        super().__init__(message, **kwargs)
        self.platform = platform
        self.url = url
        if platform:
            self.details["platform"] = platform
        if url:
            self.details["url"] = url


class YouTubeError(PlatformError):
    """Raised when YouTube operations fail"""
    
    def __init__(self, message: str, video_id: Optional[str] = None, **kwargs):
        super().__init__(message, platform="YouTube", **kwargs)
        self.video_id = video_id
        if video_id:
            self.details["video_id"] = video_id


class TwitterError(PlatformError):
    """Raised when Twitter/X operations fail"""
    
    def __init__(self, message: str, tweet_id: Optional[str] = None, **kwargs):
        super().__init__(message, platform="Twitter/X", **kwargs)
        self.tweet_id = tweet_id
        if tweet_id:
            self.details["tweet_id"] = tweet_id


class UnsupportedURLError(PlatformError):
    """Raised when URL is not supported by any platform"""
    pass


class ContentNotFoundError(PlatformError):
    """Raised when content is not found (deleted, private, etc.)"""
    pass


# Download Errors
class DownloadError(YTBotError):
    """Base exception for download-related errors"""
    
    def __init__(
        self,
        message: str,
        download_id: Optional[str] = None,
        url: Optional[str] = None,
        **kwargs
    ):
        super().__init__(message, **kwargs)
        self.download_id = download_id
        self.url = url
        if download_id:
            self.details["download_id"] = download_id
        if url:
            self.details["url"] = url


class DownloadCancelledError(DownloadError):
    """Raised when download is cancelled by user"""
    pass


class DownloadTimeoutError(DownloadError):
    """Raised when download times out"""
    pass


class FormatSelectionError(DownloadError):
    """Raised when format selection fails"""
    pass


class FFmpegError(DownloadError):
    """Raised when FFmpeg operations fail"""
    pass


# Storage Errors
class StorageError(YTBotError):
    """Base exception for storage-related errors"""
    
    def __init__(
        self,
        message: str,
        storage_type: Optional[str] = None,
        file_path: Optional[str] = None,
        **kwargs
    ):
        super().__init__(message, **kwargs)
        self.storage_type = storage_type
        self.file_path = file_path
        if storage_type:
            self.details["storage_type"] = storage_type
        if file_path:
            self.details["file_path"] = file_path


class NextcloudError(StorageError):
    """Raised when Nextcloud operations fail"""
    
    def __init__(self, message: str, **kwargs):
        super().__init__(message, storage_type="nextcloud", **kwargs)


class LocalStorageError(StorageError):
    """Raised when local storage operations fail"""
    
    def __init__(self, message: str, **kwargs):
        super().__init__(message, storage_type="local", **kwargs)


class StorageQuotaError(StorageError):
    """Raised when storage quota is exceeded"""
    pass


class FileNotFoundError(StorageError):
    """Raised when file is not found in storage"""
    pass


# Telegram Errors
class TelegramError(YTBotError):
    """Base exception for Telegram-related errors"""
    
    def __init__(
        self,
        message: str,
        chat_id: Optional[int] = None,
        **kwargs
    ):
        super().__init__(message, **kwargs)
        self.chat_id = chat_id
        if chat_id:
            self.details["chat_id"] = chat_id


class TelegramConnectionError(TelegramError):
    """Raised when Telegram connection fails"""
    pass


class TelegramAPIError(TelegramError):
    """Raised when Telegram API returns an error"""
    
    def __init__(
        self,
        message: str,
        api_error_code: Optional[int] = None,
        **kwargs
    ):
        super().__init__(message, **kwargs)
        self.api_error_code = api_error_code
        if api_error_code:
            self.details["api_error_code"] = api_error_code


class PermissionDeniedError(TelegramError):
    """Raised when user doesn't have permission"""
    pass


# State Management Errors
class StateError(YTBotError):
    """Base exception for state management errors"""
    
    def __init__(
        self,
        message: str,
        user_id: Optional[int] = None,
        **kwargs
    ):
        super().__init__(message, **kwargs)
        self.user_id = user_id
        if user_id:
            self.details["user_id"] = user_id


class StateNotFoundError(StateError):
    """Raised when user state is not found"""
    pass


class StateExpiredError(StateError):
    """Raised when user state has expired"""
    pass


# Cache Errors
class CacheError(YTBotError):
    """Base exception for cache-related errors"""
    pass


class CacheEntryNotFoundError(CacheError):
    """Raised when cache entry is not found"""
    pass


# Startup Errors
class StartupError(YTBotError):
    """Base exception for startup errors"""
    
    def __init__(
        self,
        message: str,
        phase: Optional[str] = None,
        **kwargs
    ):
        super().__init__(message, **kwargs)
        self.phase = phase
        if phase:
            self.details["phase"] = phase


class DependencyError(StartupError):
    """Raised when required dependency is missing"""
    
    def __init__(
        self,
        message: str,
        dependency: Optional[str] = None,
        install_command: Optional[str] = None,
        **kwargs
    ):
        super().__init__(message, **kwargs)
        self.dependency = dependency
        self.install_command = install_command
        if dependency:
            self.details["dependency"] = dependency
        if install_command:
            self.details["install_command"] = install_command


# Network Errors
class NetworkError(YTBotError):
    """Base exception for network-related errors"""
    
    def __init__(
        self,
        message: str,
        url: Optional[str] = None,
        status_code: Optional[int] = None,
        **kwargs
    ):
        super().__init__(message, **kwargs)
        self.url = url
        self.status_code = status_code
        if url:
            self.details["url"] = url
        if status_code:
            self.details["status_code"] = status_code


class ConnectionTimeoutError(NetworkError):
    """Raised when connection times out"""
    pass


class RetryExhaustedError(NetworkError):
    """Raised when all retry attempts are exhausted"""
    pass


def get_user_friendly_message(error: Exception) -> str:
    """
    Get user-friendly error message for an exception.
    
    Args:
        error: The exception that occurred
        
    Returns:
        User-friendly error message
    """
    if isinstance(error, ConfigValidationError):
        return f"配置错误: {error.message}"
    
    if isinstance(error, ConfigMissingError):
        return f"缺少必要配置: {error.message}"
    
    if isinstance(error, YouTubeError):
        return f"YouTube 处理失败: {error.message}"
    
    if isinstance(error, TwitterError):
        return f"Twitter/X 处理失败: {error.message}"
    
    if isinstance(error, UnsupportedURLError):
        return f"不支持的链接: {error.message}"
    
    if isinstance(error, ContentNotFoundError):
        return "内容未找到，可能已被删除或设为私有"
    
    if isinstance(error, DownloadCancelledError):
        return "下载已取消"
    
    if isinstance(error, DownloadTimeoutError):
        return "下载超时，请稍后重试"
    
    if isinstance(error, FFmpegError):
        return f"媒体处理失败: {error.message}"
    
    if isinstance(error, NextcloudError):
        return f"云存储服务暂时不可用，文件已保存到本地"
    
    if isinstance(error, LocalStorageError):
        return f"本地存储失败: {error.message}"
    
    if isinstance(error, StorageQuotaError):
        return "存储空间不足，请清理后重试"
    
    if isinstance(error, TelegramConnectionError):
        return "Telegram 连接失败，请检查网络"
    
    if isinstance(error, PermissionDeniedError):
        return "您没有权限执行此操作"
    
    if isinstance(error, StateExpiredError):
        return "操作已过期，请重新开始"
    
    if isinstance(error, DependencyError):
        msg = f"缺少必要依赖: {error.dependency}"
        if error.install_command:
            msg += f"\n请运行: {error.install_command}"
        return msg
    
    if isinstance(error, ConnectionTimeoutError):
        return "连接超时，请检查网络后重试"
    
    if isinstance(error, RetryExhaustedError):
        return "多次尝试失败，请稍后重试"
    
    # Default message for unknown errors
    return f"操作失败: {str(error)}"
