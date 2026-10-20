"""
One-shot download mode for CLI usage.

Downloads and stores content without any Telegram interaction:
each URL is downloaded and uploaded (Nextcloud via StorageService)
exactly like the bot flow, then the process exits with a summary
exit code.

Design: docs/superpowers/specs/2026-10-07-one-shot-download-design.md

After D (download pipeline convergence), this module is a thin wrapper
around ``DownloadPipeline``.  The pipeline handles format selection,
download, format fallback, storage, and temp dir cleanup.
"""

from typing import Any, Dict, Optional

from ..core.logger import get_logger
from ..services.download_pipeline import (
    DownloadPipeline,
    estimate_format_size,
    LARGE_FILE_THRESHOLD,
)

logger = get_logger(__name__)


class OneShotDownloader:
    """Download and store a URL (or YouTube playlist) without Telegram."""

    def __init__(self, download_service, storage_service):
        self.download_service = download_service
        self.storage_service = storage_service

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
        """Expand a playlist into single videos and process each one."""
        entries = await handler.get_playlist_entries(url)
        if entries is None:
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

        # One-time format selection from the first entry; all entries inherit
        shared_format_id = await self._select_format_id(
            content_type, handler, entries[0]
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
        # Large-file warning (CLI-only: warn and proceed)
        if handler.name == "YouTube" and not inherited_format_id:
            _, formats = await handler.get_format_list(url)
            file_size = estimate_format_size(formats, None)
            # Try with selected format
            if content_type == "audio":
                fid = handler.select_best_audio_format(formats)
                file_size = estimate_format_size(formats, fid)
            else:
                vf = handler.select_best_video_format(formats)
                file_size = estimate_format_size(formats, vf)
            if file_size and file_size > LARGE_FILE_THRESHOLD:
                print(
                    f"⚠️  文件较大（约 {file_size / 1024 / 1024:.1f} MB），"
                    f"跳过确认直接下载"
                )

        pipeline = DownloadPipeline(
            download_service=self.download_service,
            storage_service=self.storage_service,
            progress_sink=_TerminalProgressSink(progress_label),
        )
        success = await pipeline.run(
            url=url,
            download_type=content_type,
            handler=handler,
            inherited_format_id=inherited_format_id,
            progress_label=progress_label,
        )
        if success:
            return None
        return "下载失败"

    async def _select_format_id(
        self, content_type: str, handler, url: str
    ) -> Optional[str]:
        """Select format for a URL. Returns format_id."""
        _, formats = await handler.get_format_list(url)
        if content_type == "audio":
            return handler.select_best_audio_format(formats)
        vf = handler.select_best_video_format(formats)
        af = handler.select_best_audio_format(formats)
        if vf and af:
            return f"{vf}+{af}"
        return vf


# ---------------------------------------------------------------------------
# Terminal progress sink
# ---------------------------------------------------------------------------


class _TerminalProgressSink:
    """ProgressSink for CLI one-shot downloads."""

    def __init__(self, progress_label: Optional[str] = None) -> None:
        self._label = progress_label

    async def on_download_stage(self, text: str) -> None:
        print(text)

    async def on_download_progress(
        self, progress_data: Dict[str, Any]
    ) -> None:
        # No-op for CLI (pipeline already has terminal callback)
        pass

    async def on_upload_progress(
        self,
    ) -> Any:
        label = f"[{self._label}] " if self._label else ""
        last_time = [0.0]
        last_percent = [-100.0]

        def callback(current: int, total: int) -> None:
            if total <= 0:
                return
            percentage = current / total * 100
            now = __import__("time").monotonic()
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

        return callback

    async def on_success(
        self,
        storage_result: Dict[str, Any],
        download_type: str,
        is_twitter: bool = False,
        content_info: Optional[Dict[str, Any]] = None,
        send_as_new: bool = False,
    ) -> bool:
        if storage_result.get('cached'):
            print("📋 已存本地并进缓存队列，待 bot 后台任务重试上传")
        return True

    async def on_error(
        self,
        error_message: str,
        is_twitter: bool = False,
        content_info: Optional[Dict[str, Any]] = None,
    ) -> bool:
        print(f"❌ {error_message}")
        return False
