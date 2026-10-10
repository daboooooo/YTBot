"""Unit tests for the per-post archiver (spec 17.7/17.8/17.9).

No network, no real TwitterHandler: a scripted fake returns
DownloadResult objects from real temp directories, exactly like the
production handler does.
"""

import asyncio
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ytbot.core.types import ContentInfo, DownloadResult
from ytbot.services.x_user_archive.archiver import (
    ArchiveOptions,
    ArchivePause,
    PostArchiver,
    is_valid_content_dir,
    post_dir_name,
)
from ytbot.services.x_user_archive.models import (
    ContentKind,
    ContentRecord,
    DownloadStatus,
    SkipReason,
    snowflake_to_datetime,
)

UTC = timezone.utc
NOW = datetime(2025, 3, 1, 12, 0, tzinfo=UTC)
SID_1 = "1893214234567890123"
SID_2 = "1894000000000000456"
SID_3 = "1895000000000000789"


# Mirrors TwitterHandler._parse_twitter_error (the real method ignores
# self); production archiver uses the handler's own method.
def _parse_twitter_error(error_msg: str) -> str:
    low = error_msg.lower()
    if "登录失败" in error_msg or "重新登录" in error_msg:
        return "ERROR_LOGIN_FAILED"
    if "rate limit" in low or "速率限制" in error_msg:
        return "ERROR_RATE_LIMITED"
    if "not found" in low or "不存在" in error_msg:
        return "ERROR_TWEET_NOT_FOUND"
    if "suspended" in low or "封禁" in error_msg:
        return "ERROR_ACCOUNT_SUSPENDED"
    if "protected" in low or "私密" in error_msg:
        return "ERROR_TWEET_PROTECTED"
    return "ERROR_TWITTER_UNKNOWN"


def make_record(tweet_id=SID_1, *, status=DownloadStatus.PENDING,
                title=None, content_type=ContentKind.POST,
                is_repost=False, archive_path=None):
    published = snowflake_to_datetime(tweet_id)
    return ContentRecord(
        tweet_id=tweet_id,
        author_id="elonmusk",
        status_author="spacex" if is_repost else "elonmusk",
        post_url=(
            f"https://x.com/"
            f"{'spacex' if is_repost else 'elonmusk'}/status/{tweet_id}"
        ),
        published_at=published,
        discovered_at=NOW,
        content_type=content_type,
        title=title,
        status=status,
        archive_path=archive_path,
        is_repost=is_repost,
    )


class FakeHandler:
    """Scripted download_content; each step:
    ("ok", title=None, post_type="regular", files=("html",), empty=False,
     persist=False) | ("fail", message) | ("raise", exc)
    """

    def __init__(self, steps):
        self.steps = list(steps)
        self.calls = []
        self.temp_dirs = []

    _parse_twitter_error = staticmethod(_parse_twitter_error)

    async def download_content(self, url, content_type,
                               progress_callback=None):
        self.calls.append((url, content_type))
        step = self.steps[min(len(self.calls) - 1, len(self.steps) - 1)]
        kind = step[0]
        if kind == "raise":
            raise step[1]
        if kind == "fail":
            return DownloadResult(success=False, error_message=step[1])
        # ok
        temp = Path(tempfile.mkdtemp(prefix="faketw_"))
        self.temp_dirs.append(temp)
        files = step[2] if len(step) > 2 else ("html",)
        if "html" in files:
            (temp / f"tweet_99_{len(self.calls)}.html").write_text(
                "<html>body</html>", encoding="utf-8"
            )
        if "jpg" in files:
            (temp / "media_1.jpg").write_bytes(b"\xff\xd8\xff")
        if "pdf" in files:
            (temp / "tweet_99.pdf").write_bytes(b"%PDF-1.4 stub")
        title = step[1] if len(step) > 1 else "Hello World"
        post_type = step[3] if len(step) > 3 else "regular"
        info = ContentInfo(
            url=url,
            title=title,
            metadata={"post_type": post_type},
        )
        return DownloadResult(
            success=True, file_path=str(temp), temp_dir=str(temp),
            content_info=info,
        )


def make_archiver(handler, root, **opts):
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    options = ArchiveOptions(
        request_delay=opts.pop("request_delay", 0.0),
        max_attempts=opts.pop("max_attempts", 3),
        backoff_base=opts.pop("backoff_base", 5.0),
        backoff_cap=opts.pop("backoff_cap", 60.0),
    )
    arch = PostArchiver(handler, root, options, sleep_func=fake_sleep)
    return arch, sleeps


@pytest.fixture
def root(tmp_path):
    return tmp_path / "x_archive" / "elonmusk" / "content"


# ---------------------------------------------------------------- 17.7 naming

class TestNamingAndPlacement:
    def test_basic_success_place_by_publish_month(self, root):
        handler = FakeHandler([("ok", "First Post")])
        arch, _ = make_archiver(handler, root)
        rec = make_record(SID_1)

        status = asyncio.get_event_loop().run_until_complete(
            arch.archive(rec)
        )

        assert status == DownloadStatus.DOWNLOADED
        pub = snowflake_to_datetime(SID_1)
        expected = root / pub.strftime("%Y") / pub.strftime("%M")
        # MM must be month, not minute (guards against directive typos)
        month_dir = root / pub.strftime("%Y") / pub.strftime("%m")
        assert not expected.exists()
        dirs = list(month_dir.iterdir())
        assert len(dirs) == 1
        target = dirs[0]
        assert target.name == (
            f"{pub:%Y-%m-%d}_First Post_{SID_1[-6:]}"
        )
        assert (target / "tweet_99_1.html").exists()
        assert Path(rec.archive_path) == target.resolve()
        assert rec.title == "First Post"

    def test_empty_title_falls_back_to_post_id(self, root):
        handler = FakeHandler([("ok", None)])
        arch, _ = make_archiver(handler, root)
        rec = make_record(SID_2)

        status = asyncio.get_event_loop().run_until_complete(
            arch.archive(rec)
        )

        assert status == DownloadStatus.DOWNLOADED
        target = Path(rec.archive_path)
        # date_post_<id>_<short>
        assert target.name == (
            f"{snowflake_to_datetime(SID_2):%Y-%m-%d}_"
            f"post_{SID_2}_{SID_2[-6:]}"
        )

    def test_article_fallback_prefix(self, root):
        handler = FakeHandler([("ok", None, ("html",), "article")])
        arch, _ = make_archiver(handler, root)
        rec = make_record(SID_2, content_type=ContentKind.UNKNOWN)

        asyncio.get_event_loop().run_until_complete(arch.archive(rec))

        assert rec.content_type == ContentKind.ARTICLE
        target = Path(rec.archive_path)
        assert target.name.split("_", 2)[1] == "article"

    def test_title_with_separators_and_newlines_sanitized(self, root):
        handler = FakeHandler([("ok", "a/b\\c:d\ne|f?g*h")])
        arch, _ = make_archiver(handler, root)
        rec = make_record(SID_3)

        asyncio.get_event_loop().run_until_complete(arch.archive(rec))

        target = Path(rec.archive_path)
        # exactly content_root/YYYY/MM/<dir>: no extra nesting
        assert target.parent.parent.name == snowflake_to_datetime(
            SID_3
        ).strftime("%Y")
        for ch in '/\\:\n|?*<>':
            assert ch not in target.name

    def test_long_title_byte_truncated(self, root):
        long_title = "长" * 200
        handler = FakeHandler([("ok", long_title)])
        arch, _ = make_archiver(handler, root)
        rec = make_record(SID_1)

        asyncio.get_event_loop().run_until_complete(arch.archive(rec))

        name = Path(rec.archive_path).name.encode("utf-8")
        assert len(name) <= 255
        # title fragment alone is capped by the 80-byte budget
        slug = name.decode().split("_", 1)[1].rsplit("_", 1)[0]
        assert len(slug.encode("utf-8")) <= 80

    def test_rerun_is_skipped_without_network(self, root):
        handler = FakeHandler([("ok", "Stable"), ("ok", "Stable2")])
        arch, _ = make_archiver(handler, root)
        rec = make_record(SID_1)

        loop = asyncio.new_event_loop()
        first = loop.run_until_complete(arch.archive(rec))
        rec2 = make_record(SID_1, status=DownloadStatus.DOWNLOADED,
                           title=rec.title, archive_path=rec.archive_path)
        second = loop.run_until_complete(arch.archive(rec2))

        assert first == DownloadStatus.DOWNLOADED
        assert second == DownloadStatus.SKIPPED
        assert len(handler.calls) == 1
        assert rec2.skip_reason == SkipReason.ALREADY_ARCHIVED

    def test_downloaded_manifest_but_dir_gone_redownloads(self, root):
        handler = FakeHandler([("ok", "X"), ("ok", "X")])
        arch, _ = make_archiver(handler, root)
        loop = asyncio.new_event_loop()
        loop.run_until_complete(arch.archive(make_record(SID_1)))
        import shutil
        shutil.rmtree(root)

        rec = make_record(SID_1, status=DownloadStatus.DOWNLOADED,
                          title="X")
        status = loop.run_until_complete(arch.archive(rec))

        assert status == DownloadStatus.DOWNLOADED
        assert len(handler.calls) == 2

    def test_downloading_residue_is_replaced(self, root):
        handler = FakeHandler([("ok", "X"), ("ok", "X")])
        arch, _ = make_archiver(handler, root)
        loop = asyncio.new_event_loop()
        first_rec = make_record(SID_1)
        loop.run_until_complete(arch.archive(first_rec))
        target = Path(first_rec.archive_path)
        (target / "stale.tmp").write_text("residue")

        rec = make_record(SID_1, status=DownloadStatus.DOWNLOADING,
                          title="X")
        status = loop.run_until_complete(arch.archive(rec))

        assert status == DownloadStatus.DOWNLOADED
        assert not (target / "stale.tmp").exists()
        assert not list(target.parent.glob("*.obsolete.*"))

    def test_traversal_title_cannot_escape_root(self, root):
        handler = FakeHandler([("ok", "../../../../etc/passwd")])
        arch, _ = make_archiver(handler, root)
        rec = make_record(SID_1)

        asyncio.get_event_loop().run_until_complete(arch.archive(rec))

        target = Path(rec.archive_path).resolve()
        assert str(target).startswith(str(root.resolve()))
        assert (target / "tweet_99_1.html").exists()

    def test_other_media_files_moved_alongside_html(self, root):
        handler = FakeHandler([("ok", "Pic", ("html", "jpg"))])
        arch, _ = make_archiver(handler, root)
        rec = make_record(SID_1)

        asyncio.get_event_loop().run_until_complete(arch.archive(rec))

        target = Path(rec.archive_path)
        assert (target / "media_1.jpg").exists()


# ---------------------------------------------------------------- 17.8 retries

class TestRetryClassification:
    def test_rate_limit_then_success_backoff_delays(self, root):
        handler = FakeHandler([
            ("fail", "rate limit exceeded"),
            ("fail", "Rate limit"),
            ("ok", "Late"),
        ])
        arch, sleeps = make_archiver(
            handler, root, backoff_base=5.0
        )
        rec = make_record(SID_1)

        status = asyncio.new_event_loop().run_until_complete(
            arch.archive(rec)
        )

        assert status == DownloadStatus.DOWNLOADED
        assert len(handler.calls) == 3
        assert len(sleeps) == 2
        assert 5.0 <= sleeps[0] < 5.6
        assert 10.0 <= sleeps[1] < 11.1
        assert rec.attempts == 3

    def test_attempts_exhausted_marks_retryable(self, root):
        handler = FakeHandler([("fail", "rate limit")] * 5)
        arch, sleeps = make_archiver(
            handler, root, max_attempts=3, backoff_base=5.0
        )
        rec = make_record(SID_1)

        status = asyncio.new_event_loop().run_until_complete(
            arch.archive(rec)
        )

        assert status == DownloadStatus.RETRYABLE_ERROR
        assert len(handler.calls) == 3
        assert rec.error_code == "ERROR_RATE_LIMITED"

    def test_timeout_exception_is_retryable(self, root):
        handler = FakeHandler([
            ("raise", asyncio.TimeoutError()),
            ("ok", "Recovered"),
        ])
        arch, sleeps = make_archiver(handler, root)
        rec = make_record(SID_1)

        status = asyncio.new_event_loop().run_until_complete(
            arch.archive(rec)
        )

        assert status == DownloadStatus.DOWNLOADED
        assert len(handler.calls) == 2

    def test_login_failure_is_systemic_pause(self, root):
        handler = FakeHandler([("fail", "登录失败，请重新登录")])
        arch, _ = make_archiver(handler, root)
        rec = make_record(SID_1)

        with pytest.raises(ArchivePause) as exc:
            asyncio.new_event_loop().run_until_complete(arch.archive(rec))

        assert exc.value.code == "ERROR_LOGIN_FAILED"
        assert exc.value.reason == "login_failed"
        assert rec.status == DownloadStatus.RETRYABLE_ERROR
        assert len(handler.calls) == 1

    @pytest.mark.parametrize("message,code", [
        ("tweet not found", "ERROR_TWEET_NOT_FOUND"),
        ("账号已封禁 suspended", "ERROR_ACCOUNT_SUSPENDED"),
        ("protected account 私密", "ERROR_TWEET_PROTECTED"),
    ])
    def test_permanent_errors_not_retried(self, root, message, code):
        handler = FakeHandler([("fail", message)])
        arch, sleeps = make_archiver(handler, root)
        rec = make_record(SID_1)

        status = asyncio.new_event_loop().run_until_complete(
            arch.archive(rec)
        )

        assert status == DownloadStatus.PERMANENT_ERROR
        assert rec.error_code == code
        assert len(handler.calls) == 1
        assert sleeps == []

    def test_empty_product_counts_as_failed_attempt(self, root):
        handler = FakeHandler([
            ("ok", "Empty", ()),
            ("fail", "rate limit"),
            ("ok", "Good"),
        ])
        arch, _ = make_archiver(handler, root)
        rec = make_record(SID_1)

        status = asyncio.new_event_loop().run_until_complete(
            arch.archive(rec)
        )

        assert status == DownloadStatus.DOWNLOADED
        assert len(handler.calls) == 3

    def test_no_html_product_treated_as_failure(self, root):
        handler = FakeHandler([
            ("ok", "OnlyJpg", ("jpg",)),
        ] * 3)
        arch, _ = make_archiver(handler, root, max_attempts=2)
        rec = make_record(SID_1)

        status = asyncio.new_event_loop().run_until_complete(
            arch.archive(rec)
        )

        assert status == DownloadStatus.RETRYABLE_ERROR
        assert rec.error_code == "ERROR_NO_HTML"
        assert len(handler.calls) == 2


# ------------------------------------------------------------------ 17.9 flow

class TestPipeline:
    def test_multiple_records_each_get_polite_delay(self, root):
        handler = FakeHandler([
            ("ok", "One"), ("ok", "Two"), ("ok", "Three"),
        ])
        sleeps = []

        async def fake_sleep(seconds):
            sleeps.append(seconds)

        arch = PostArchiver(
            handler, root,
            ArchiveOptions(request_delay=2.0),
            sleep_func=fake_sleep,
        )
        recs = [make_record(SID_1), make_record(SID_2),
                make_record(SID_3)]

        async def run():
            statuses = []
            for rec in recs:
                statuses.append(await arch.archive(rec))
                await arch.polite_delay()
            return statuses

        statuses = asyncio.new_event_loop().run_until_complete(run())

        assert statuses == [DownloadStatus.DOWNLOADED] * 3
        assert len(sleeps) == 3
        for delay in sleeps:
            assert 2.0 <= delay <= 2.5
        # distinct month/date dirs, temp dirs all moved out
        targets = {Path(r.archive_path) for r in recs}
        assert len(targets) == 3
        for temp in handler.temp_dirs:
            assert not temp.exists()

    def test_existing_valid_target_skipped_even_if_manifest_pending(
        self, root
    ):
        # pre-create a valid target dir under the expected name
        pub = snowflake_to_datetime(SID_1)
        rec = make_record(SID_1)
        name = post_dir_name(rec, "Hello World")
        target = root / pub.strftime("%Y") / pub.strftime("%m") / name
        target.mkdir(parents=True)
        (target / "existing.html").write_text("<html/>")

        handler = FakeHandler([("ok", "Hello World")])
        arch, _ = make_archiver(handler, root)

        status = asyncio.new_event_loop().run_until_complete(
            arch.archive(rec)
        )

        assert status == DownloadStatus.SKIPPED
        assert (target / "existing.html").exists()
        # freshly downloaded temp product is discarded, not merged
        for temp in handler.temp_dirs:
            assert not temp.exists()

    def test_invalid_existing_dir_removed_and_redownloaded(self, root):
        pub = snowflake_to_datetime(SID_1)
        rec = make_record(SID_1)
        name = post_dir_name(rec, "Hello World")
        target = root / pub.strftime("%Y") / pub.strftime("%m") / name
        target.mkdir(parents=True)
        (target / "junk.bin").write_bytes(b"x")

        handler = FakeHandler([("ok", "Hello World")])
        arch, _ = make_archiver(handler, root)

        status = asyncio.new_event_loop().run_until_complete(
            arch.archive(rec)
        )

        assert status == DownloadStatus.DOWNLOADED
        assert not (target / "junk.bin").exists()
        assert is_valid_content_dir(target)
