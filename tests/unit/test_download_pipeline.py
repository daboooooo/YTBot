"""
Unit tests for DownloadPipeline stage messages.

回归覆盖：X 文章下载（download_type="text"）不得提示“正在下载音频”。
"""

import pytest

from ytbot.core.types import DownloadResult
from ytbot.services.download_pipeline import DownloadPipeline


class RecordingSink:
    """记录 on_download_stage 文案，其余回调全部空实现。"""

    def __init__(self):
        self.stage_text = None

    async def on_download_stage(self, text):
        self.stage_text = text

    async def on_download_progress(self, progress_data):
        pass

    async def on_upload_progress(self):
        return lambda current, total: None

    async def on_success(self, *args, **kwargs):
        return True

    async def on_error(self, *args, **kwargs):
        return False


class FakeHandler:
    name = "Twitter/X"


class FakeDownloadService:
    async def download_content(self, **kwargs):
        return DownloadResult(success=True, file_path="/tmp/x.html")


class FakeStorageService:
    async def store_file(self, *args, **kwargs):
        return {
            "success": True,
            "storage_type": "nextcloud",
            "file_url": "http://example.test/x",
        }


@pytest.mark.parametrize(
    ("download_type", "expected_fragment"),
    [
        ("audio", "正在下载音频"),
        ("video", "正在下载"),
        ("text", "正在保存内容"),
        ("image", "正在下载图片"),
    ],
)
async def test_stage_text_matches_download_type(
    download_type, expected_fragment
):
    sink = RecordingSink()
    pipeline = DownloadPipeline(
        download_service=FakeDownloadService(),
        storage_service=FakeStorageService(),
        progress_sink=sink,
    )

    result = await pipeline.run(
        url="https://x.com/u/status/1",
        download_type=download_type,
        handler=FakeHandler(),
        content_info={"success": True},
    )

    assert result is True
    assert sink.stage_text is not None
    assert expected_fragment in sink.stage_text
    if download_type != "audio":
        assert "音频" not in sink.stage_text
