"""
Unit tests for playlist expansion in TelegramHandler._proceed_with_download

Verifies that a YouTube playlist URL is expanded into single-video URLs
and each entry is processed exactly like a normal link: download one,
upload one (instead of downloading everything and uploading only one file).
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from ytbot.handlers.telegram_handler import TelegramHandler
from ytbot.core.types import ContentType, ContentInfo, DownloadResult


PLAYLIST_URL = "https://www.youtube.com/playlist?list=PL123"

ENTRY_URLS = [
    "https://www.youtube.com/watch?v=abc123",
    "https://www.youtube.com/watch?v=def456",
    "https://www.youtube.com/watch?v=ghi789",
]


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


def make_download_result(url, ok=True, title="Video"):
    if ok:
        return DownloadResult(
            success=True,
            file_path="/tmp/fake.mp4",
            content_info=ContentInfo(
                url=url, title=title, content_type=ContentType.VIDEO
            ),
            temp_dir=None
        )
    return DownloadResult(success=False, error_message="失败", temp_dir=None)


@pytest.fixture
def telegram_handler():
    """Create a TelegramHandler with all dependencies mocked"""
    handler = TelegramHandler(
        telegram_service=MagicMock(),
        storage_service=MagicMock(),
        download_service=MagicMock()
    )
    handler.telegram_service.edit_message = AsyncMock(
        return_value={'message_id': 1}
    )
    handler.telegram_service.send_message = AsyncMock(
        return_value={'message_id': 2}
    )
    handler.storage_service.store_file = AsyncMock(return_value={
        'success': True,
        'storage_type': 'nextcloud',
        'file_url': 'https://nc.example.com/fake',
    })
    return handler


class TestPlaylistExpansion:

    @pytest.mark.asyncio
    async def test_playlist_processes_each_entry(self, telegram_handler):
        """Each playlist entry is downloaded and uploaded separately"""
        yh = make_youtube_handler(ENTRY_URLS)
        telegram_handler.download_service.platform_manager.get_handler = \
            MagicMock(return_value=yh)
        telegram_handler.download_service.download_content = AsyncMock(
            side_effect=lambda url, **kwargs: make_download_result(url)
        )

        result = await telegram_handler._proceed_with_download(
            chat_id=100, message_id=1, url=PLAYLIST_URL,
            download_type="video", handler=yh
        )

        assert result is True
        # One download per entry, using plain watch URLs
        assert telegram_handler.download_service.download_content.await_count == 3
        called_urls = [
            call.kwargs.get('url') or call.args[0]
            for call in telegram_handler.download_service.download_content.await_args_list
        ]
        assert called_urls == ENTRY_URLS
        # One upload per entry
        assert telegram_handler.storage_service.store_file.await_count == 3
        # Summary message sent
        sent = [
            call.kwargs.get('text', '')
            for call in telegram_handler.telegram_service.send_message.await_args_list
        ]
        assert any('成功 3' in text and '失败 0' in text for text in sent)

    @pytest.mark.asyncio
    async def test_entry_urls_have_no_list_param(self, telegram_handler):
        """Entry URLs passed to download must not contain 'list='
        (prevents recursive playlist expansion)"""
        yh = make_youtube_handler(ENTRY_URLS)
        telegram_handler.download_service.platform_manager.get_handler = \
            MagicMock(return_value=yh)
        telegram_handler.download_service.download_content = AsyncMock(
            side_effect=lambda url, **kwargs: make_download_result(url)
        )

        await telegram_handler._proceed_with_download(
            chat_id=100, message_id=1, url=PLAYLIST_URL,
            download_type="video", handler=yh
        )

        for call in telegram_handler.download_service.download_content.await_args_list:
            url = call.kwargs.get('url') or call.args[0]
            assert 'list=' not in url

    @pytest.mark.asyncio
    async def test_playlist_entry_failure_continues(self, telegram_handler):
        """A failed entry does not stop processing of the remaining ones"""
        yh = make_youtube_handler(ENTRY_URLS)
        telegram_handler.download_service.platform_manager.get_handler = \
            MagicMock(return_value=yh)

        results = [
            make_download_result(ENTRY_URLS[0]),
            make_download_result(ENTRY_URLS[1], ok=False),
            make_download_result(ENTRY_URLS[2]),
        ]
        telegram_handler.download_service.download_content = AsyncMock(
            side_effect=lambda url, **kwargs: results.pop(0)
        )

        result = await telegram_handler._proceed_with_download(
            chat_id=100, message_id=1, url=PLAYLIST_URL,
            download_type="video", handler=yh
        )

        assert result is False
        # All 3 entries were still attempted
        assert telegram_handler.download_service.download_content.await_count == 3
        # Uploads only for the 2 successful entries
        assert telegram_handler.storage_service.store_file.await_count == 2
        sent = [
            call.kwargs.get('text', '')
            for call in telegram_handler.telegram_service.send_message.await_args_list
        ]
        assert any('成功 2' in text and '失败 1' in text for text in sent)

    @pytest.mark.asyncio
    async def test_playlist_skips_large_file_confirmation(self, telegram_handler):
        """Playlist mode processes entries without the 500MB confirmation"""
        yh = make_youtube_handler(ENTRY_URLS)
        # Formats that estimate > 500MB for every entry
        yh.get_format_list = AsyncMock(return_value=({}, [
            {'format_id': '137', 'vcodec': 'avc1', 'acodec': 'none',
             'filesize': 600 * 1024 * 1024},
            {'format_id': '140', 'acodec': 'aac', 'vcodec': 'none',
             'filesize': 10 * 1024 * 1024},
        ]))
        yh.select_best_video_format = MagicMock(return_value='137')
        yh.select_best_audio_format = MagicMock(return_value='140')
        telegram_handler.download_service.platform_manager.get_handler = \
            MagicMock(return_value=yh)
        telegram_handler.download_service.download_content = AsyncMock(
            side_effect=lambda url, **kwargs: make_download_result(url)
        )
        telegram_handler._ask_large_file_confirmation = AsyncMock()

        result = await telegram_handler._proceed_with_download(
            chat_id=100, message_id=1, url=PLAYLIST_URL,
            download_type="video", handler=yh
        )

        assert result is True
        # No confirmation asked; all entries downloaded directly
        assert telegram_handler._ask_large_file_confirmation.await_count == 0
        assert telegram_handler.download_service.download_content.await_count == 3

    @pytest.mark.asyncio
    async def test_single_video_large_file_still_confirms(self, telegram_handler):
        """Non-playlist large files still trigger the confirmation"""
        yh = make_youtube_handler(None)
        yh.is_playlist = MagicMock(return_value=False)
        yh.get_format_list = AsyncMock(return_value=({}, [
            {'format_id': '137', 'vcodec': 'avc1', 'acodec': 'none',
             'filesize': 600 * 1024 * 1024},
            {'format_id': '140', 'acodec': 'aac', 'vcodec': 'none',
             'filesize': 10 * 1024 * 1024},
        ]))
        yh.select_best_video_format = MagicMock(return_value='137')
        yh.select_best_audio_format = MagicMock(return_value='140')
        telegram_handler.download_service.platform_manager.get_handler = \
            MagicMock(return_value=yh)
        telegram_handler.download_service.download_content = AsyncMock()
        telegram_handler._ask_large_file_confirmation = AsyncMock()

        result = await telegram_handler._proceed_with_download(
            chat_id=100, message_id=1,
            url="https://www.youtube.com/watch?v=abc123",
            download_type="video", handler=yh
        )

        assert result is False
        assert telegram_handler._ask_large_file_confirmation.await_count == 1
        assert telegram_handler.download_service.download_content.await_count == 0

    @pytest.mark.asyncio
    async def test_playlist_expansion_failure_falls_back(self, telegram_handler):
        """Expansion failure falls back to whole-playlist download"""
        yh = make_youtube_handler(None)  # None = expansion failed
        telegram_handler.download_service.platform_manager.get_handler = \
            MagicMock(return_value=yh)
        telegram_handler.download_service.download_content = AsyncMock(
            return_value=make_download_result(PLAYLIST_URL)
        )

        result = await telegram_handler._proceed_with_download(
            chat_id=100, message_id=1, url=PLAYLIST_URL,
            download_type="video", handler=yh
        )

        assert result is True
        # Single download call with the original playlist URL
        assert telegram_handler.download_service.download_content.await_count == 1
        call = telegram_handler.download_service.download_content.await_args_list[0]
        url = call.kwargs.get('url') or call.args[0]
        assert url == PLAYLIST_URL

    @pytest.mark.asyncio
    async def test_empty_playlist_reports_and_stops(self, telegram_handler):
        """Empty playlist reports an error and downloads nothing"""
        yh = make_youtube_handler([])
        telegram_handler.download_service.platform_manager.get_handler = \
            MagicMock(return_value=yh)
        telegram_handler.download_service.download_content = AsyncMock()

        result = await telegram_handler._proceed_with_download(
            chat_id=100, message_id=1, url=PLAYLIST_URL,
            download_type="video", handler=yh
        )

        assert result is False
        assert telegram_handler.download_service.download_content.await_count == 0
        edited = [
            call.kwargs.get('text', '')
            for call in telegram_handler.telegram_service.edit_message.await_args_list
        ]
        assert any('播放列表为空' in text for text in edited)

    @pytest.mark.asyncio
    async def test_playlist_entry_success_sent_as_new_message(
        self, telegram_handler
    ):
        """Each entry's success message is sent as a new message so results
        are not overwritten by the next entry"""
        yh = make_youtube_handler(ENTRY_URLS)
        telegram_handler.download_service.platform_manager.get_handler = \
            MagicMock(return_value=yh)
        telegram_handler.download_service.download_content = AsyncMock(
            side_effect=lambda url, **kwargs: make_download_result(url)
        )

        await telegram_handler._proceed_with_download(
            chat_id=100, message_id=1, url=PLAYLIST_URL,
            download_type="video", handler=yh
        )

        # 3 success messages sent via send_message (not edit_message):
        # one per entry + the summary
        sent_texts = [
            call.kwargs.get('text', '')
            for call in telegram_handler.telegram_service.send_message.await_args_list
        ]
        assert len([t for t in sent_texts if '下载和上传完成' in t]) == 3
        # Success messages must not be sent via edit_message
        edited_texts = [
            call.kwargs.get('text', '')
            for call in telegram_handler.telegram_service.edit_message.await_args_list
        ]
        assert not any('下载和上传完成' in text for text in edited_texts)


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
