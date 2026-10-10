"""
Unit tests for Nextcloud upload progress callbacks.

Verifies that:
- upload_file uses the low-level webdav API (which forwards per-chunk
  progress) when a callback is given, and keeps upload_sync otherwise
- byte offsets/grand totals aggregate correctly for directory uploads
"""

import os
from unittest.mock import MagicMock, call

import pytest

from ytbot.storage import nextcloud_storage as nc_module
from ytbot.storage.nextcloud_storage import NextcloudStorage


@pytest.fixture
def storage(monkeypatch):
    """NextcloudStorage without __init__ network connection"""
    monkeypatch.setattr(
        nc_module,
        'CONFIG',
        {
            'nextcloud': {
                'url': 'https://nc.example.com',
                'username': 'user',
                'upload_retries': 1,
                'upload_retry_delay': 0,
            }
        },
    )
    s = NextcloudStorage.__new__(NextcloudStorage)
    s.client = MagicMock()
    s._verify_upload = MagicMock(return_value=True)
    s._ensure_directory_exists = MagicMock()
    return s


class TestUploadFileProgress:

    def test_uses_low_level_api_with_progress(self, tmp_path, storage):
        f = tmp_path / "a.mp4"
        f.write_bytes(b"x" * 1000)
        progress = MagicMock()

        result = storage.upload_file(
            str(f), "/YTBot/a.mp4", progress_callback=progress
        )

        assert result is not None
        storage.client.upload_file.assert_called_once()
        storage.client.upload_sync.assert_not_called()

        # Simulate per-chunk callbacks from the webdav library
        lib_cb = storage.client.upload_file.call_args.kwargs["progress"]
        lib_cb(0, 1000)
        lib_cb(400, 1000)
        lib_cb(1000, 1000)

        assert progress.call_args_list == [
            call(0, 1000),
            call(400, 1000),
            call(1000, 1000),
        ]

    def test_without_callback_uses_upload_sync(self, tmp_path, storage):
        f = tmp_path / "a.mp4"
        f.write_bytes(b"x" * 1000)

        storage.upload_file(str(f), "/YTBot/a.mp4")

        storage.client.upload_sync.assert_called_once()
        storage.client.upload_file.assert_not_called()

    def test_offset_and_grand_total_aggregation(self, tmp_path, storage):
        f = tmp_path / "a.mp4"
        f.write_bytes(b"x" * 1000)
        progress = MagicMock()

        storage.upload_file(
            str(f), "/YTBot/a.mp4",
            progress_callback=progress,
            byte_offset=2000,
            total_bytes=5000,
        )

        lib_cb = storage.client.upload_file.call_args.kwargs["progress"]
        lib_cb(400, 1000)

        progress.assert_called_with(2400, 5000)


class TestUploadDirectoryProgress:

    def test_aggregates_bytes_across_files(self, tmp_path, storage):
        d = tmp_path / "tweet"
        d.mkdir()
        (d / "a.txt").write_bytes(b"a" * 1000)
        (d / "b.txt").write_bytes(b"b" * 500)

        progress = MagicMock()

        def fake_upload_file(local_path, remote_path,
                             progress_callback=None, byte_offset=0,
                             total_bytes=None):
            # Simulate 50% of this file transmitted
            size = os.path.getsize(local_path)
            progress_callback(byte_offset + size // 2, total_bytes)
            return f"https://nc.example.com{remote_path}"

        storage.upload_file = MagicMock(side_effect=fake_upload_file)

        result = storage.upload_directory(
            str(d), "/YTBot/tweet", progress_callback=progress
        )

        assert result["success"] is True
        assert storage.upload_file.call_count == 2

        # Every file sees the grand total (1500 bytes); offsets must form a
        # contiguous chain of cumulative file sizes regardless of walk order
        expected_midpoints = [(0, 1500)]
        cumulative = 0
        for c in storage.upload_file.call_args_list:
            assert c.kwargs["total_bytes"] == 1500
            assert c.kwargs["byte_offset"] == cumulative
            size = os.path.getsize(c.kwargs["local_path"])
            # fake simulates 50% of the file transmitted
            expected_midpoints.append((cumulative + size // 2, 1500))
            cumulative += size
        assert cumulative == 1500

        # Initial zero callback + one simulated mid-upload per file
        reported = [c.args for c in progress.call_args_list]
        for pair in expected_midpoints:
            assert pair in reported
