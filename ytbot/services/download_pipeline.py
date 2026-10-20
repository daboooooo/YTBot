"""
Download pipeline – shared orchestration between Bot and CLI flows.

Extracts the common download → format fallback → store → cleanup sequence
so that ``telegram_handler._proceed_with_download`` and
``OneShotDownloader._download_and_store`` are thin wrappers over a
single implementation.

The Bot and CLI differ in two orthogonal concerns:
* **Progress reporting**  – terminal ``print()`` vs Telegram ``edit_message``
* **Large-file policy**  – warn-and-continue (CLI) vs confirm-before-download (Bot)

These are captured as protocol / callback parameters so the pipeline stays
generic while both callers get the right behaviour.
"""

import os
import time
from typing import (
    Any, Callable, Dict, List, Optional, Protocol,
)

from ..core.logger import get_logger
from ..core.types import DownloadResult
from ..utils.common import sanitize_filename

logger = get_logger(__name__)

LARGE_FILE_THRESHOLD = 500 * 1024 * 1024  # 500 MB


# ---------------------------------------------------------------------------
# Progress reporting protocol
# ---------------------------------------------------------------------------


class ProgressSink(Protocol):
    """End-points for download / upload progress feedback."""

    async def on_download_stage(self, text: str) -> None:
        """Show a download stage message (e.g. "Downloading…")."""

    async def on_download_progress(
        self, progress_data: Dict[str, Any]
    ) -> None:
        """Called periodically with yt-dlp progress data."""

    async def on_upload_progress(
        self,
    ) -> Callable[[int, int], None]:
        """Return a sync upload-progress callback.

        The returned callable takes ``(current, total)`` bytes and should
        be invoked synchronously by the caller (e.g. via the storage
        service's ``store_file`` progress parameter).
        """

    async def on_success(
        self, storage_result: Dict[str, Any],
        download_type: str,
        is_twitter: bool = False,
        content_info: Optional[Dict[str, Any]] = None,
        send_as_new: bool = False,
    ) -> bool:
        """Handle success. Returns True on success, False otherwise."""

    async def on_error(
        self, error_message: str, is_twitter: bool = False,
        content_info: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Handle download error. Returns False."""


# ---------------------------------------------------------------------------
# Large-file policy callback
# ---------------------------------------------------------------------------

# Signature: (chat_id, message_id, url, download_type, content_info,
#             file_size_bytes, format_id) -> bool
# Returns True if download should proceed, False if cancelled.
LargeFilePolicy = Callable[
    [int, int, str, str, Optional[Dict[str, Any]], int, Optional[str]],
    bool,
]

# ---------------------------------------------------------------------------
# Shared utility functions
# ---------------------------------------------------------------------------


def build_target_filename(
    file_path: str, title: str, download_type: str
) -> str:
    """Build target filename (same rules used by both Bot and CLI)."""
    if os.path.isdir(file_path):
        return sanitize_filename(title)
    _, original_ext = os.path.splitext(file_path)
    ext = original_ext or (
        '.mp3' if download_type == 'audio' else '.mp4'
    )
    return sanitize_filename(f"{title}{ext}")


def estimate_format_size(
    formats: List[Dict[str, Any]], format_id: Optional[str]
) -> Optional[int]:
    """Estimate total size of a format selection in bytes.

    Shared between ``one_shot_download`` and ``telegram_handler``.
    """
    if not formats or not format_id:
        return None

    def _fmt_size(fmt: Dict[str, Any]) -> Optional[int]:
        return (
            fmt.get('filesize')
            or fmt.get('filesize_estimate')
            or fmt.get('filesize_approx')
        )

    format_id_str = str(format_id)
    if '+' in format_id_str:
        video_id, audio_id = format_id_str.split('+')
        sizes = []
        for fmt in formats:
            fmt_id = str(fmt.get('format_id', ''))
            if fmt_id in (video_id, audio_id):
                size = _fmt_size(fmt)
                if size:
                    sizes.append(size)
        return sum(sizes) if sizes else None

    for fmt in formats:
        if str(fmt.get('format_id', '')) == format_id_str:
            return _fmt_size(fmt)

    # Fallback: video-only format
    for fmt in formats:
        if fmt.get('vcodec') != 'none' and fmt.get('acodec') == 'none':
            return (
                fmt.get('filesize')
                or fmt.get('filesize_estimate')
                or fmt.get('filesize_approx')
            )
    return None


# ---------------------------------------------------------------------------
# Internal helpers (called by DownloadPipeline)
# ---------------------------------------------------------------------------


def _make_terminal_progress_callback(
    progress_label: Optional[str] = None,
) -> Callable[[Dict[str, Any]], None]:
    """Create a throttled terminal download progress callback."""
    label = f"[{progress_label}] " if progress_label else ""
    last_time = [0.0]  # use list to allow mutation in nested func
    last_percent = [-100.0]

    def progress_callback(progress_data: Dict[str, Any]) -> None:
        if progress_data.get('status') != 'downloading':
            return
        downloaded = progress_data.get('downloaded_bytes', 0)
        total = (
            progress_data.get('total_bytes')
            or progress_data.get('total_bytes_estimate', 0)
        )
        if total <= 0:
            return
        percentage = downloaded / total * 100
        now = time.monotonic()
        if (
            now - last_time[0] < 5.0
            and abs(percentage - last_percent[0]) < 10.0
        ):
            return
        speed = progress_data.get('speed') or 0
        speed_str = f", {speed / 1024 / 1024:.1f} MB/s" if speed else ""
        print(
            f"{label}{percentage:.1f}% "
            f"({downloaded / 1024 / 1024:.1f}/"
            f"{total / 1024 / 1024:.1f} MB{speed_str})"
        )
        last_time[0] = now
        last_percent[0] = percentage

    return progress_callback


def _make_terminal_upload_progress_callback(
    progress_label: Optional[str] = None,
) -> Callable[[int, int], None]:
    """Create a throttled terminal upload progress callback (sync)."""
    label = f"[{progress_label}] " if progress_label else ""
    last_time = [0.0]
    last_percent = [-100.0]

    def upload_callback(current: int, total: int) -> None:
        if total <= 0:
            return
        percentage = current / total * 100
        now = time.monotonic()
        if (
            current < total
            and now - last_time[0] < 5.0
            and abs(percentage - last_percent[0]) < 10.0
        ):
            return
        print(
            f"{label}☁️ 上传中 {percentage:.1f}% "
            f"({current / 1024 / 1024:.1f}/"
            f"{total / 1024 / 1024:.1f} MB)"
        )
        last_time[0] = now
        last_percent[0] = percentage

    return upload_callback


# ---------------------------------------------------------------------------
# DownloadPipeline
# ---------------------------------------------------------------------------


class DownloadPipeline:
    """Orchestrates download → format fallback → store → cleanup.

    Parameters
    ----------
    download_service:
        The ``DownloadService`` instance (singleton).
    storage_service:
        The ``StorageService`` instance (singleton).
    progress_sink:
        Controls how progress / success / error messages are emitted.
    large_file_policy:
        ``None`` for "no confirmation" (CLI), or a callback for Bot.
    """

    def __init__(
        self,
        download_service: Any,
        storage_service: Any,
        progress_sink: ProgressSink,
        large_file_policy: Optional[LargeFilePolicy] = None,
        archive_mode: bool = False,
    ) -> None:
        self._download_service = download_service
        self._storage_service = storage_service
        self._progress_sink = progress_sink
        self._large_file_policy = large_file_policy
        self._archive_mode = archive_mode

    async def run(
        self,
        url: str,
        download_type: str,
        handler: Any,
        content_info: Optional[Dict[str, Any]] = None,
        inherited_format_id: Optional[str] = None,
        progress_label: Optional[str] = None,
    ) -> bool:
        """Execute a single download-and-store.

        Returns ``True`` on success, ``False`` on failure.
        """
        download_result: Optional[DownloadResult] = None
        is_twitter = handler.name == "Twitter/X"
        twitter_result = (
            content_info if is_twitter and content_info
            and content_info.get('success') else None
        )

        # 1. Format selection (YouTube-specific; other platforms ignore it)
        format_id: Optional[str] = None
        formats: List[Dict[str, Any]] = []
        file_size_estimate: Optional[int] = None

        if handler.name == "YouTube" and inherited_format_id:
            format_id = inherited_format_id
        elif handler.name == "YouTube":
            _, formats = await handler.get_format_list(url)
            if download_type == "audio":
                format_id = handler.select_best_audio_format(formats)
                file_size_estimate = estimate_format_size(formats, format_id)
            else:
                vfmt = handler.select_best_video_format(formats)
                afmt = handler.select_best_audio_format(formats)
                if vfmt and afmt:
                    format_id = f"{vfmt}+{afmt}"
                elif vfmt:
                    format_id = vfmt
                file_size_estimate = estimate_format_size(formats, format_id)

        # 2. Large-file check (Bot only)
        if self._large_file_policy:
            is_large_file = (
                handler.name == "YouTube"
                and download_type == "video"
                and file_size_estimate is not None
                and file_size_estimate > LARGE_FILE_THRESHOLD
            )
            if is_large_file:
                # Policy callback returns True=proceed, False=cancel
                cancelled = not self._large_file_policy(
                    url, download_type, content_info,
                    file_size_estimate, format_id,
                )
                if cancelled:
                    return False

        # 3. Progress callback
        progress_callback = _make_terminal_progress_callback(progress_label)

        # 4. Download
        stage_text_by_type = {
            "audio": "⬇️ 正在下载音频",
            "video": "⬇️ 正在下载",
            "text": "⬇️ 正在保存内容",
            "image": "🖼️ 正在下载图片",
        }
        stage_text = stage_text_by_type.get(
            download_type, "⬇️ 正在下载"
        )
        if progress_label:
            stage_text += f" ({progress_label})"
        stage_text += "..."
        await self._progress_sink.on_download_stage(stage_text)

        download_result = await self._download_service.download_content(
            url=url,
            content_type=download_type,
            progress_callback=progress_callback,
            format_id=format_id,
            handler=handler,
            pre_scraped_result=twitter_result,
            archive_mode=self._archive_mode,
        )

        # 5. Inherited format fallback (YouTube playlist entries)
        if (
            not download_result.success
            and inherited_format_id
            and download_result.error_detail == "ERROR_FORMAT_NOT_AVAILABLE"
        ):
            logger.warning(
                f"Inherited format {inherited_format_id} not available "
                f"for {url}, retrying with default format"
            )
            download_result = await self._download_service.download_content(
                url=url,
                content_type=download_type,
                progress_callback=progress_callback,
                format_id=None,
                handler=handler,
                pre_scraped_result=twitter_result,
                archive_mode=self._archive_mode,
            )

        if not download_result.success:
            await self._progress_sink.on_error(
                download_result.error_message or "下载失败",
                is_twitter=is_twitter,
                content_info=content_info,
            )
            return False

        # 6. Determine actual content type and build filename
        if download_result.content_info:
            title = download_result.content_info.title
            actual_type = download_result.content_info.content_type.value
        elif content_info:
            title = content_info.get('title', 'Unknown')
            actual_type = download_type
        else:
            title = 'Unknown'
            actual_type = download_type

        target_filename = build_target_filename(
            download_result.file_path, title, actual_type
        )

        # 7. Store
        upload_progress = await self._progress_sink.on_upload_progress()
        storage_result = await self._storage_service.store_file(
            download_result.file_path,
            target_filename,
            actual_type,
            progress_callback=upload_progress,
        )

        if storage_result.get('success'):
            success = await self._progress_sink.on_success(
                storage_result, actual_type,
                is_twitter=is_twitter,
                content_info=content_info,
            )
            return success
        else:
            await self._progress_sink.on_error(
                "文件存储失败，请稍后重试。",
                is_twitter=is_twitter,
                content_info=content_info,
            )
            return False

        # 8. Cleanup (done by caller in finally)
