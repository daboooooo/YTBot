"""
Type definitions for YTBot

Provides type aliases, TypeVars, and Protocols for type-safe code.
"""

from typing import (
    TypeVar, Dict, Any, Optional, List, Callable, Awaitable, 
    Union, Protocol, runtime_checkable, Tuple
)
from enum import Enum
from dataclasses import dataclass
from pathlib import Path


# Type variables
T = TypeVar('T')
K = TypeVar('K')
V = TypeVar('V')
R = TypeVar('R')


# Content types
class ContentType(Enum):
    """Supported content types"""
    VIDEO = "video"
    AUDIO = "audio"
    IMAGE = "image"
    TEXT = "text"
    PLAYLIST = "playlist"


# Type aliases
JSONDict = Dict[str, Any]
Headers = Dict[str, str]
ProgressCallback = Callable[[Dict[str, Any]], Awaitable[None]]
SyncProgressCallback = Callable[[Dict[str, Any]], None]
PathLike = Union[str, Path]
ChatId = Union[int, str]
FormatId = str


@dataclass
class ContentInfo:
    """Information about downloadable content"""
    url: str
    title: str
    description: Optional[str] = None
    duration: Optional[int] = None  # seconds
    content_type: ContentType = ContentType.VIDEO
    thumbnail_url: Optional[str] = None
    uploader: Optional[str] = None
    upload_date: Optional[str] = None
    file_size_estimate: Optional[int] = None  # bytes
    formats: Optional[List[JSONDict]] = None
    metadata: Optional[JSONDict] = None


@dataclass
class DownloadResult:
    """Result of a download operation"""
    success: bool
    file_path: Optional[str] = None
    content_info: Optional[ContentInfo] = None
    error_message: Optional[str] = None
    cancelled: bool = False


@dataclass
class StorageResult:
    """Result of a storage operation"""
    success: bool
    storage_type: Optional[str] = None
    file_path: Optional[str] = None
    file_url: Optional[str] = None
    error: Optional[str] = None
    cached: bool = False


@dataclass
class FormatInfo:
    """Information about a download format"""
    format_id: str
    ext: str
    resolution: Optional[str] = None
    fps: Optional[int] = None
    bitrate: Optional[int] = None  # kbps
    filesize: Optional[int] = None  # bytes
    vcodec: Optional[str] = None
    acodec: Optional[str] = None
    quality: Optional[str] = None


@dataclass
class CacheEntry:
    """Cache entry for pending uploads"""
    file_path: str
    filename: str
    content_type: str
    timestamp: str
    metadata: JSONDict


# Protocols for interface definitions
@runtime_checkable
class LoggerProtocol(Protocol):
    """Protocol for logger objects"""
    
    def debug(self, msg: str, *args: Any, **kwargs: Any) -> None: ...
    def info(self, msg: str, *args: Any, **kwargs: Any) -> None: ...
    def warning(self, msg: str, *args: Any, **kwargs: Any) -> None: ...
    def error(self, msg: str, *args: Any, **kwargs: Any) -> None: ...
    def critical(self, msg: str, *args: Any, **kwargs: Any) -> None: ...
    def exception(self, msg: str, *args: Any, **kwargs: Any) -> None: ...


@runtime_checkable
class PlatformHandlerProtocol(Protocol):
    """Protocol for platform handlers"""
    
    name: str
    supported_content_types: List[ContentType]
    
    def can_handle(self, url: str) -> bool: ...
    async def get_content_info(self, url: str) -> Optional[ContentInfo]: ...
    async def download_content(
        self,
        url: str,
        content_type: ContentType,
        progress_callback: Optional[ProgressCallback] = None,
        format_id: Optional[str] = None
    ) -> DownloadResult: ...
    def get_supported_formats(self, url: str) -> List[JSONDict]: ...


@runtime_checkable
class StorageBackendProtocol(Protocol):
    """Protocol for storage backends"""
    
    async def store_file(
        self,
        source_path: str,
        filename: str,
        content_type: str = "media"
    ) -> StorageResult: ...
    
    async def delete_file(self, file_path: str) -> bool: ...
    
    def is_available(self) -> bool: ...
    
    def get_storage_info(self) -> JSONDict: ...


@runtime_checkable
class TelegramServiceProtocol(Protocol):
    """Protocol for Telegram service"""
    
    async def connect(self) -> bool: ...
    async def disconnect(self) -> None: ...
    async def send_message(
        self,
        chat_id: ChatId,
        text: str,
        **kwargs: Any
    ) -> JSONDict: ...
    async def edit_message(
        self,
        chat_id: ChatId,
        message_id: int,
        text: str,
        **kwargs: Any
    ) -> JSONDict: ...
    def check_user_permission(self, chat_id: ChatId) -> bool: ...


# User state types
class UserState(Enum):
    """User interaction states"""
    IDLE = "idle"
    WAITING_DOWNLOAD_TYPE = "waiting_download_type"
    WAITING_CONFIRMATION = "waiting_confirmation"
    WAITING_TEXT_CONFIRMATION = "waiting_text_confirmation"
    DOWNLOADING = "downloading"
    ERROR = "error"


StateData = JSONDict
UserStateInfo = Tuple[UserState, StateData, float]  # state, data, timestamp


# Progress types
ProgressStatus = str  # "downloading", "finished", "error"


# Startup phase types
class StartupPhase(Enum):
    """Startup phases"""
    CONFIG_VALIDATION = "config_validation"
    FFMPEG_CHECK = "ffmpeg_check"
    YT_DLP_UPDATE = "yt_dlp_update"
    TELEGRAM_CONNECTION = "telegram_connection"
    NEXTCLOUD_CONNECTION = "nextcloud_connection"
    LOCAL_STORAGE_INIT = "local_storage_init"
    CACHE_CHECK = "cache_check"
    MESSAGE_LISTENER = "message_listener"


class PhaseStatus(Enum):
    """Phase execution status"""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    ROLLED_BACK = "rolled_back"
