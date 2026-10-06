"""
One-shot download mode for CLI usage.

Downloads and stores content without any Telegram interaction:
each URL is downloaded and uploaded (Nextcloud via StorageService)
exactly like the bot flow, then the process exits with a summary
exit code.

Design: docs/superpowers/specs/2026-10-07-one-shot-download-design.md
"""

import os
import shutil
import time
from typing import Any, Dict, List, Optional

from ..core.logger import get_logger
from ..utils.common import sanitize_filename

logger = get_logger(__name__)

LARGE_FILE_THRESHOLD = 500 * 1024 * 1024  # 500MB


def _estimate_file_size(
    formats: List[Dict[str, Any]], format_id: Optional[str]
) -> Optional[int]:
    """Estimate the total size of a format selection in bytes"""
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
    return None


class OneShotDownloader:
    """Download and store a URL (or YouTube playlist) without Telegram."""

    def __init__(self, download_service, storage_service):
        self.download_service = download_service
        self.storage_service = storage_service
        self._last_progress_time = 0.0
        self._last_progress_percent = -100.0

    async def run(self, url: str, content_type: str) -> int:
        """Download and store one URL. Returns process exit code."""
        handler = self.download_service.platform_manager.get_handler(url)
        if not handler:
            print(f"❌ 无法处理此链接：{url}")
            return 1

        if handler.name == "YouTube" and handler.is_playlist(url):
            return await self._run_playlist(url, content_type, handler)

        error = await self._download_and_store(url, content_type, handler)
        return 0 if error is None else 1

    async def _run_playlist(
        self, url: str, content_type: str, handler
    ) -> int:
        """Expand a playlist into single videos and process each one
        exactly like the bot: download one, upload one."""
        entries = await handler.get_playlist_entries(url)
        if entries is None:
            # 展开失败：回退整列表直接下载（playlist URL 的格式选择为空，
            # yt-dlp 以默认格式整包下载，存储按目录处理）
            logger.warning(
                f"Failed to expand playlist, falling back to "
                f"whole-playlist download: {url}"
            )
            error = await self._download_and_store(url, content_type, handler)
            return 0 if error is None else 1

        if not entries:
            print("❌ 播放列表为空或没有可下载的视频。")
            return 1

        total = len(entries)
        print(f"📋 检测到播放列表，共 {total} 个视频，将逐个下载并上传...")

        # 一次性共享格式选择（取自首个视频），所有 entry 继承
        shared_format_id, _ = await self._select_format(
            entries[0], content_type, handler
        )

        succeeded = 0
        failures = []
        for i, entry_url in enumerate(entries, 1):
            error = await self._download_and_store(
                entry_url, content_type, handler,
                inherited_format_id=shared_format_id,
                progress_label=f"{i}/{total}",
            )
            if error is None:
                succeeded += 1
            else:
                failures.append((entry_url, error))

        failed = total - succeeded
        print(f"📋 播放列表处理完成：成功 {succeeded} / 失败 {failed} / 共 {total}")
        for entry_url, error in failures:
            print(f"   ❌ {entry_url}: {error}")
        return 0 if failed == 0 else 1

    async def _download_and_store(
        self,
        url: str,
        content_type: str,
        handler,
        inherited_format_id: Optional[str] = None,
        progress_label: Optional[str] = None,
    ) -> Optional[str]:
        """Download one URL and store it.

        Returns None on success, or an error message on failure.
        """
        download_result = None
        try:
            format_id = inherited_format_id
            if handler.name == "YouTube" and not inherited_format_id:
                format_id, formats = await self._select_format(
                    url, content_type, handler
                )
                # CLI 模式无确认交互：大文件打警告后直接下载
                size_estimate = _estimate_file_size(formats, format_id)
                if size_estimate and size_estimate > LARGE_FILE_THRESHOLD:
                    print(
                        f"⚠️  文件较大（约 {size_estimate / 1024 / 1024:.1f} MB），"
                        f"跳过确认直接下载"
                    )

            progress_callback = self._make_progress_callback(progress_label)

            download_result = await self.download_service.download_content(
                url=url,
                content_type=content_type,
                progress_callback=progress_callback,
                format_id=format_id,
                handler=handler,
            )

            # 继承格式对该视频不可用：以默认格式选择重试一次
            if (
                not download_result.success
                and inherited_format_id
                and download_result.error_detail == "ERROR_FORMAT_NOT_AVAILABLE"
            ):
                logger.warning(
                    f"Inherited format {inherited_format_id} not available "
                    f"for {url}, retrying with default format"
                )
                download_result = await self.download_service.download_content(
                    url=url,
                    content_type=content_type,
                    progress_callback=progress_callback,
                    format_id=None,
                    handler=handler,
                )

            if not download_result.success:
                return download_result.error_message or "下载失败"

            title = (
                download_result.content_info.title
                if download_result.content_info else "Unknown"
            )
            actual_type = (
                download_result.content_info.content_type.value
                if download_result.content_info
                and download_result.content_info.content_type
                else content_type
            )

            storage_result = await self.storage_service.store_file(
                download_result.file_path,
                self._build_filename(
                    download_result.file_path, title, actual_type
                ),
                actual_type,
            )

            if storage_result.get('success'):
                if storage_result.get('cached'):
                    print("📋 已存本地并进缓存队列，待 bot 后台任务重试上传")
                return None
            return storage_result.get('error') or "文件存储失败"

        except Exception as e:
            logger.exception(f"One-shot download error: {url}")
            return str(e)
        finally:
            if download_result and download_result.temp_dir:
                try:
                    if os.path.exists(download_result.temp_dir):
                        shutil.rmtree(download_result.temp_dir, ignore_errors=True)
                except Exception as cleanup_err:
                    logger.warning(f"Failed to clean up temp dir: {cleanup_err}")

    async def _select_format(
        self, url: str, content_type: str, handler
    ):
        """Select format for a URL. Returns (format_id, formats)."""
        _, formats = await handler.get_format_list(url)
        if content_type == "audio":
            return handler.select_best_audio_format(formats), formats
        video_format = handler.select_best_video_format(formats)
        audio_format = handler.select_best_audio_format(formats)
        if video_format and audio_format:
            return f"{video_format}+{audio_format}", formats
        return video_format, formats

    def _build_filename(
        self, file_path: str, title: str, download_type: str
    ) -> str:
        """Build the target filename (same rules as the bot)"""
        if os.path.isdir(file_path):
            return sanitize_filename(title)
        _, original_ext = os.path.splitext(file_path)
        ext = original_ext or (
            '.mp3' if download_type == 'audio' else '.mp4'
        )
        return sanitize_filename(f"{title}{ext}")

    def _make_progress_callback(self, progress_label: Optional[str] = None):
        """Create a throttled terminal progress callback"""
        label = f"[{progress_label}] " if progress_label else ""

        async def progress_callback(progress_data: Dict[str, Any]):
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
            # 节流：每 5 秒或进度变化 10% 才打印一行
            if (
                now - self._last_progress_time < 5.0
                and abs(percentage - self._last_progress_percent) < 10.0
            ):
                return
            speed = progress_data.get('speed') or 0
            speed_str = f", {speed / 1024 / 1024:.1f} MB/s" if speed else ""
            print(
                f"{label}{percentage:.1f}% "
                f"({downloaded / 1024 / 1024:.1f}/"
                f"{total / 1024 / 1024:.1f} MB{speed_str})"
            )
            self._last_progress_time = now
            self._last_progress_percent = percentage

        return progress_callback
