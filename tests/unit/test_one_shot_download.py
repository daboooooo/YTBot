"""
Unit tests for OneShotDownloader (one-shot CLI download mode)

Verifies download+store orchestration without any Telegram interaction:
playlists are expanded into single videos, each entry is downloaded and
uploaded one by one, matching the bot's playlist semantics.
"""

import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from ytbot.core.types import ContentType, ContentInfo, DownloadResult
from ytbot.services.one_shot_download import OneShotDownloader


PLAYLIST_URL = "https://www.youtube.com/playlist?list=PL123"

ENTRY_URLS = [
    "https://www.youtube.com/watch?v=abc123",
    "https://www.youtube.com/watch?v=def456",
    "https://www.youtube.com/watch?v=ghi789",
]

REPO_ROOT = Path(__file__).resolve().parents[2]


def make_youtube_handler(entries):
    """Create a mocked YouTube handler with playlist expansion behavior"""
    h = MagicMock()
    h.name = "YouTube"
    h.is_playlist = MagicMock(
        side_effect=lambda url: 'list=' in url or '/playlist?' in url
    )
    h.get_playlist_entries = AsyncMock(return_value=entries)
    h.get_format_list = AsyncMock(return_value=({}, []))
    h.select_best_video_format = MagicMock(return_value=None)
    h.select_best_audio_format = MagicMock(return_value=None)
    return h


def make_download_result(url, ok=True, title="Video", error_detail=None):
    if ok:
        return DownloadResult(
            success=True,
            file_path="/tmp/fake.mp4",
            content_info=ContentInfo(
                url=url, title=title, content_type=ContentType.VIDEO
            ),
            temp_dir=None
        )
    return DownloadResult(
        success=False,
        error_message="失败",
        error_detail=error_detail,
        temp_dir=None
    )


def make_downloader(store_result=None):
    """Create a OneShotDownloader with download/storage services mocked"""
    download_service = MagicMock()
    storage_service = MagicMock()
    if store_result is None:
        store_result = {
            'success': True,
            'storage_type': 'nextcloud',
            'file_url': 'https://nc.example.com/fake',
        }
    storage_service.store_file = AsyncMock(return_value=store_result)
    return (
        OneShotDownloader(download_service, storage_service),
        download_service,
        storage_service,
    )


class TestSingleVideo:

    @pytest.mark.asyncio
    async def test_single_video_success(self):
        downloader, ds, ss = make_downloader()
        yh = make_youtube_handler(ENTRY_URLS)
        ds.platform_manager.get_handler = MagicMock(return_value=yh)
        ds.download_content = AsyncMock(
            side_effect=lambda url, **kwargs: make_download_result(url)
        )

        exit_code = await downloader.run(ENTRY_URLS[0], "video")

        assert exit_code == 0
        assert ds.download_content.await_count == 1
        ss.store_file.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_handler_returns_error(self):
        downloader, ds, ss = make_downloader()
        ds.platform_manager.get_handler = MagicMock(return_value=None)
        ds.download_content = AsyncMock()

        exit_code = await downloader.run("https://example.com/x", "video")

        assert exit_code == 1
        ds.download_content.assert_not_awaited()


class TestPlaylist:

    @pytest.mark.asyncio
    async def test_playlist_processes_each_entry_with_shared_format(self):
        downloader, ds, ss = make_downloader()
        yh = make_youtube_handler(ENTRY_URLS)
        yh.select_best_video_format = MagicMock(return_value='137')
        yh.select_best_audio_format = MagicMock(return_value='140')
        ds.platform_manager.get_handler = MagicMock(return_value=yh)
        ds.download_content = AsyncMock(
            side_effect=lambda url, **kwargs: make_download_result(url)
        )

        exit_code = await downloader.run(PLAYLIST_URL, "video")

        assert exit_code == 0
        # One download and one upload per entry
        assert ds.download_content.await_count == 3
        assert ss.store_file.await_count == 3
        # Format selection happens once (first entry), entries inherit it
        assert yh.get_format_list.await_count == 1
        for call in ds.download_content.await_args_list:
            assert call.kwargs.get('format_id') == '137+140'

    @pytest.mark.asyncio
    async def test_empty_playlist_returns_error(self):
        downloader, ds, ss = make_downloader()
        yh = make_youtube_handler([])
        ds.platform_manager.get_handler = MagicMock(return_value=yh)
        ds.download_content = AsyncMock()

        exit_code = await downloader.run(PLAYLIST_URL, "video")

        assert exit_code == 1
        ds.download_content.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_playlist_expansion_failure_falls_back_to_whole_download(self):
        downloader, ds, ss = make_downloader()
        yh = make_youtube_handler(None)
        ds.platform_manager.get_handler = MagicMock(return_value=yh)
        ds.download_content = AsyncMock(
            side_effect=lambda url, **kwargs: make_download_result(url)
        )

        exit_code = await downloader.run(PLAYLIST_URL, "video")

        assert exit_code == 0
        # Whole playlist URL is downloaded directly (no entry expansion)
        ds.download_content.assert_awaited_once()
        assert ds.download_content.await_args.kwargs.get('url') == PLAYLIST_URL
