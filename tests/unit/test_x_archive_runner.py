"""Runner orchestration and CLI validation tests (spec 17.3/17.10/17.11/17.12).

Everything network/browser related is injected: a scripted handler
served by the REAL PostArchiver, a fake enumerator yielding prepared
records, and a fake merge step.
"""

import argparse
import asyncio
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from ytbot.core.types import ContentInfo, DownloadResult
from ytbot.services.x_user_archive.manifest import (
    ManifestStore,
    TaskState,
    TaskStateStore,
)
from ytbot.services.x_user_archive.models import (
    ContentKind,
    ContentRecord,
    DownloadStatus,
    EnumStatus,
    EnumStopReason,
    snowflake_to_datetime,
)
from ytbot.services.x_user_archive.pdf_merger import MergeResult
from ytbot.services.x_user_archive.runner import (
    EXIT_FAILURE,
    EXIT_PARTIAL,
    EXIT_SUCCESS,
    XUserArchiveRunner,
    add_archive_arguments,
    validate_archive_args,
)
from ytbot.services.x_user_archive.timeline import (
    EnumerationOutcome,
    TimelineEnumerationError,
)

UTC = timezone.utc
FIXED_NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
SID_A = "1893214234567890123"
SID_B = "1894000000000000456"
SID_C = "1895000000000000789"


def make_args(tmp_path, **over):
    base = dict(
        archive_x_user="elonmusk",
        history_years=5,
        start_at=None,
        end_at=None,
        archive_dir=str(tmp_path / "x_archive"),
        dry_run=False,
        enum_only=False,
        refresh_enum=False,
        resume=None,
        include_replies=False,
        no_reposts=False,
        max_posts=1000,
        scroll_stall=8,
        request_delay=0.0,
        no_merged_pdf=False,
        merged_pdf=None,
        download=None,
    )
    base.update(over)
    return SimpleNamespace(**base)


def make_record(tweet_id, status=DownloadStatus.PENDING, **kw):
    published = snowflake_to_datetime(tweet_id)
    author = kw.pop("status_author", "elonmusk")
    return ContentRecord(
        tweet_id=tweet_id,
        author_id="elonmusk",
        status_author=author,
        post_url=f"https://x.com/{author}/status/{tweet_id}",
        published_at=published,
        discovered_at=FIXED_NOW,
        content_type=kw.pop("content_type", ContentKind.POST),
        status=status,
        **kw,
    )


# --------------------------------------------------------------------- fakes

def _parse_twitter_error(msg):
    low = msg.lower()
    if "登录" in msg:
        return "ERROR_LOGIN_FAILED"
    if "not found" in low:
        return "ERROR_TWEET_NOT_FOUND"
    return "ERROR_TWITTER_UNKNOWN"


class ScriptedHandler:
    """Real temp-dir products, per-id failure scripts."""

    def __init__(self, behavior=None):
        self.behavior = behavior or {}
        self.calls = []
        self.extractor = object()

    _parse_twitter_error = staticmethod(_parse_twitter_error)

    async def download_content(self, url, content_type,
                               progress_callback=None):
        tweet_id = url.rsplit("/", 1)[-1]
        self.calls.append(tweet_id)
        action = self.behavior.get(tweet_id, ("ok", f"Title {tweet_id}"))
        if action[0] == "fail":
            return DownloadResult(success=False, error_message=action[1])
        if action[0] == "raise":
            raise action[1]
        temp = Path(tempfile.mkdtemp(prefix="scripttw_"))
        (temp / f"tweet_{tweet_id}_1.html").write_text(
            "<html>x</html>", encoding="utf-8"
        )
        return DownloadResult(
            success=True, file_path=str(temp), temp_dir=str(temp),
            content_info=ContentInfo(
                url=url, title=action[1],
                metadata={"post_type": "regular"},
            ),
        )


class FakeEnumerator:
    def __init__(self, records, outcome, raise_error=None):
        self._records = records
        self.outcome = outcome
        self._raise = raise_error
        self._accepted_ids = {r.tweet_id for r in records}

    async def enumerate(self):
        if self._raise is not None:
            raise self._raise
        if self._records:
            yield list(self._records)


def enum_factory(records, outcome, raise_error=None):
    def factory(extractor, options, sleep_func, preload_ids):
        return FakeEnumerator(records, outcome, raise_error)
    return factory


class MergeSpy:
    def __init__(self, result=None):
        self.calls = []
        self.result = result or MergeResult(
            status="success", output_path=None,
            included_posts=3, downloadable_posts=3, pages=5,
        )

    async def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return self.result


def complete_outcome(n):
    return EnumerationOutcome(
        stop_reason=EnumStopReason.START_BOUNDARY,
        enum_status=EnumStatus.COMPLETE,
        raw_discovered=n, in_scope_count=n,
        articles_channel="present",
    )


def partial_outcome(n):
    return EnumerationOutcome(
        stop_reason=EnumStopReason.STALL,
        enum_status=EnumStatus.PARTIAL,
        raw_discovered=n, in_scope_count=n,
        articles_channel="present",
    )


def make_runner(handler, spy, *, outcome=None, records=None,
                enum_error=None, scrape=None):
    records = records if records is not None else [
        make_record(SID_A), make_record(SID_B), make_record(SID_C)
    ]
    outcome = outcome or complete_outcome(len(records))
    return XUserArchiveRunner(
        handler_factory=lambda handle, args: handler,
        enumerator_factory=enum_factory(records, outcome, enum_error),
        merge_func=spy,
        scrape_status=scrape,
        sleep_func=lambda d: asyncio.sleep(0),
        clock=lambda: FIXED_NOW,
    )


# --------------------------------------------------------------------- tests

class TestCliValidation:
    def _parser(self):
        parser = argparse.ArgumentParser()
        parser.add_argument("--download", default=None)
        parser.add_argument("--type", default=None)
        add_archive_arguments(parser)
        return parser

    def _parse(self, parser, *argv):
        args = parser.parse_args(list(argv))
        validate_archive_args(parser, args)
        return args

    def test_defaults(self):
        args = self._parse(self._parser(), "--archive-x-user", "bob")
        assert args.max_posts == 1000
        assert args.request_delay == 2.0
        assert args.archive_dir == "./x_archive"
        assert args.no_reposts is False
        assert args.no_merged_pdf is False

    def test_download_and_archive_mutually_exclusive(self):
        with pytest.raises(SystemExit):
            self._parse(
                self._parser(),
                "--download", "https://x", "--archive-x-user", "bob",
            )

    def test_resume_rejects_window_args(self):
        with pytest.raises(SystemExit):
            self._parse(
                self._parser(),
                "--resume", "tid", "--start-at", "2025-01-01",
            )
        with pytest.raises(SystemExit):
            self._parse(
                self._parser(), "--resume", "tid", "--history-years", "3"
            )

    def test_empty_window_errors(self):
        with pytest.raises(SystemExit):
            self._parse(
                self._parser(),
                "--archive-x-user", "bob",
                "--start-at", "2026-01-01T00:00:00Z",
                "--end-at", "2025-01-01T00:00:00Z",
            )

    def test_negative_max_posts_errors(self):
        with pytest.raises(SystemExit):
            self._parse(
                self._parser(),
                "--archive-x-user", "bob", "--max-posts", "-1",
            )

    def test_zero_max_posts_allowed(self):
        args = self._parse(
            self._parser(),
            "--archive-x-user", "bob", "--max-posts", "0",
        )
        assert args.max_posts == 0

    def test_merged_pdf_flags_conflict(self):
        with pytest.raises(SystemExit):
            self._parse(
                self._parser(),
                "--archive-x-user", "bob",
                "--no-merged-pdf", "--merged-pdf", "out.pdf",
            )

    def test_refresh_enum_allowed_without_resume(self):
        args = self._parse(
            self._parser(),
            "--archive-x-user", "bob", "--refresh-enum",
        )
        assert args.refresh_enum is True

    def test_enum_only_conflicts_with_resume(self):
        with pytest.raises(SystemExit):
            self._parse(
                self._parser(),
                "--archive-x-user", "bob",
                "--resume", "tid", "--enum-only",
            )


class TestRunnerHappyPath:
    def test_dry_run_no_download_no_merge(self, tmp_path):
        handler = ScriptedHandler()
        spy = MergeSpy()
        runner = make_runner(handler, spy)
        args = make_args(tmp_path, dry_run=True)

        code = runner.run(args)

        assert code == EXIT_SUCCESS
        assert handler.calls == []
        assert spy.calls == []
        task_dirs = list((Path(args.archive_dir) / "elonmusk" / "tasks").iterdir())
        task_dir = task_dirs[0]
        assert (task_dir / "task_state.json").exists()
        assert (task_dir / "manifest.jsonl").exists()
        report = task_dir / "reports" / f"{task_dir.name}.json"
        data = __import__("json").loads(report.read_text())
        assert data["dry_run"] is True
        assert data["unique"] == 3
        # nothing downloaded
        assert not list(task_dir.glob("20*/*/*.html"))

    def test_complete_run_exit_zero_and_artifacts(self, tmp_path):
        handler = ScriptedHandler()
        spy = MergeSpy()
        runner = make_runner(handler, spy)
        args = make_args(tmp_path)

        code = runner.run(args)

        assert code == EXIT_SUCCESS
        assert handler.calls == [SID_A, SID_B, SID_C]  # chronological
        assert len(spy.calls) == 1
        assert spy.calls[0]["enum_status"] == EnumStatus.COMPLETE
        from ytbot.services.x_user_archive.manifest import TaskPaths
        task_root = Path(args.archive_dir) / "elonmusk" / "tasks"
        chosen = list(task_root.iterdir())[0]
        saved = TaskStateStore(
            TaskPaths(Path(args.archive_dir), "elonmusk", chosen.name)
        ).load()
        assert saved.downloaded == 3
        assert saved.download_status == "completed"
        assert saved.merged_pdf["status"] == "success"

    def test_partial_enumeration_exit_two(self, tmp_path):
        handler = ScriptedHandler()
        spy = MergeSpy()
        records = [make_record(SID_A), make_record(SID_B)]
        runner = make_runner(
            handler, spy, outcome=partial_outcome(2), records=records
        )
        args = make_args(tmp_path)

        code = runner.run(args)

        assert code == EXIT_PARTIAL
        assert len(handler.calls) == 2
        assert spy.calls[0]["enum_status"] == EnumStatus.PARTIAL

    def test_no_merged_pdf_does_not_merge(self, tmp_path):
        handler = ScriptedHandler()
        spy = MergeSpy()
        runner = make_runner(handler, spy)
        args = make_args(tmp_path, no_merged_pdf=True)

        code = runner.run(args)

        assert code == EXIT_SUCCESS
        assert spy.calls == []

    def test_enum_only_complete_snapshot_exit_zero(self, tmp_path):
        handler = ScriptedHandler()
        spy = MergeSpy()
        runner = make_runner(handler, spy)
        args = make_args(tmp_path, enum_only=True)

        code = runner.run(args)

        assert code == EXIT_SUCCESS
        assert handler.calls == []          # no downloads
        assert spy.calls == []              # no merged book
        task_dir = list(
            (Path(args.archive_dir) / "elonmusk" / "tasks").iterdir()
        )[0]
        assert (task_dir / "manifest.jsonl").exists()
        report = task_dir / "reports" / f"{task_dir.name}.json"
        data = __import__("json").loads(report.read_text())
        assert data["enum_only"] is True
        assert data["dry_run"] is False
        assert data["manifest_trustable"] is True
        assert data["unique"] == 3

    def test_enum_only_partial_snapshot_exit_two(self, tmp_path):
        handler = ScriptedHandler()
        spy = MergeSpy()
        records = [make_record(SID_A), make_record(SID_B)]
        runner = make_runner(
            handler, spy, outcome=partial_outcome(2), records=records
        )
        args = make_args(tmp_path, enum_only=True)

        code = runner.run(args)

        assert code == EXIT_PARTIAL
        assert handler.calls == []
        task_dir = list(
            (Path(args.archive_dir) / "elonmusk" / "tasks").iterdir()
        )[0]
        data = __import__("json").loads(
            (task_dir / "reports" / f"{task_dir.name}.json").read_text()
        )
        assert data["enum_only"] is True
        assert data["manifest_trustable"] is False


class TestFailures:
    def test_permanent_post_failure_exit_one_merge_still_runs(self, tmp_path):
        handler = ScriptedHandler({
            SID_B: ("fail", "tweet not found"),
        })
        spy = MergeSpy()
        runner = make_runner(handler, spy)
        args = make_args(tmp_path)

        code = runner.run(args)

        assert code == EXIT_FAILURE
        # processing continued past the failure
        assert handler.calls == [SID_A, SID_B, SID_C]
        assert len(spy.calls) == 1

    def test_merged_failure_exit_one(self, tmp_path):
        handler = ScriptedHandler()
        spy = MergeSpy(MergeResult(status="failed", message="boom"))
        runner = make_runner(handler, spy)
        args = make_args(tmp_path)

        code = runner.run(args)

        assert code == EXIT_FAILURE

    def test_pypdf_missing_skipped_exit_one_with_hint(self, tmp_path):
        handler = ScriptedHandler()
        spy = MergeSpy(MergeResult(
            status="skipped", message="pip install pypdf ...",
        ))
        runner = make_runner(handler, spy)
        args = make_args(tmp_path)

        code = runner.run(args)

        assert code == EXIT_FAILURE

    def test_login_failure_pauses_task_no_merge(self, tmp_path):
        handler = ScriptedHandler({
            SID_A: ("fail", "登录失败，请重新登录"),
        })
        spy = MergeSpy()
        runner = make_runner(handler, spy)
        args = make_args(tmp_path)

        code = runner.run(args)

        assert code == EXIT_FAILURE
        assert handler.calls == [SID_A]
        assert spy.calls == []
        # state persisted as paused for --resume
        root = Path(args.archive_dir) / "elonmusk" / "tasks"
        task_dir = list(root.iterdir())[0]
        import json
        state = json.loads((task_dir / "task_state.json").read_text())
        assert state["download_status"] == "paused"
        assert state["last_error"]

    def test_enumeration_systemic_failure_exit_one(self, tmp_path):
        handler = ScriptedHandler()
        spy = MergeSpy()
        err = TimelineEnumerationError(
            EnumStopReason.LOGIN_WALL, "login wall"
        )
        runner = make_runner(handler, spy, enum_error=err)
        args = make_args(tmp_path)

        code = runner.run(args)

        assert code == EXIT_FAILURE
        assert handler.calls == []
        assert spy.calls == []
        root = Path(args.archive_dir) / "elonmusk" / "tasks"
        task_dir = list(root.iterdir())[0]
        import json
        state = json.loads((task_dir / "task_state.json").read_text())
        assert state["enum_status"] == EnumStatus.FAILED


class TestStatusUrlAndResume:
    def test_status_url_uses_scrape_to_confirm_author(self, tmp_path):
        handler = ScriptedHandler()
        spy = MergeSpy()

        async def scrape(url):
            return {"success": True, "author": "@elonmusk"}

        runner = make_runner(handler, spy, scrape=scrape)
        args = make_args(
            tmp_path,
            archive_x_user=f"https://x.com/elonmusk/status/{SID_A}",
            dry_run=True,
        )

        code = runner.run(args)
        assert code == EXIT_SUCCESS

    def test_status_url_scrape_failure_aborts(self, tmp_path):
        handler = ScriptedHandler()
        spy = MergeSpy()

        async def scrape(url):
            return {"success": False, "error": "tweet not found"}

        runner = make_runner(handler, spy, scrape=scrape)
        args = make_args(
            tmp_path,
            archive_x_user=f"https://x.com/elonmusk/status/{SID_A}",
        )

        assert runner.run(args) == EXIT_FAILURE

    def test_resume_skips_done_redownloads_residue_and_pending(
        self, tmp_path
    ):
        from ytbot.services.x_user_archive.archiver import post_dir_name
        from ytbot.services.x_user_archive.manifest import TaskPaths

        archive_root = str(tmp_path / "x_archive")
        paths = TaskPaths.create(archive_root, "elonmusk", now=FIXED_NOW)

        rec_done = make_record(SID_A, DownloadStatus.DOWNLOADED, title="Done")
        rec_residue = make_record(
            SID_B, DownloadStatus.DOWNLOADING, title="Mid"
        )
        rec_pending = make_record(SID_C, DownloadStatus.PENDING)

        done_dir = paths.content_root / rec_done.published_at.strftime("%Y") \
            / rec_done.published_at.strftime("%m") \
            / post_dir_name(rec_done, "Done")
        done_dir.mkdir(parents=True)
        (done_dir / "old.html").write_text("<html/>")
        rec_done.archive_path = str(done_dir)

        residue_dir = paths.content_root / rec_residue.published_at.strftime("%Y") \
            / rec_residue.published_at.strftime("%m") \
            / post_dir_name(rec_residue, "Mid")
        residue_dir.mkdir(parents=True)
        (residue_dir / "stale.tmp").write_text("x")
        rec_residue.archive_path = str(residue_dir)

        records = [rec_done, rec_residue, rec_pending]
        state = TaskState(
            task_id=paths.task_id, author_id="elonmusk",
            start_at="2021-10-10T00:00:00+00:00",
            end_at="2026-10-10T00:00:00+00:00",
            enum_status=EnumStatus.COMPLETE,
            enum_stop_reason=EnumStopReason.START_BOUNDARY,
            unique=3,
        )
        TaskStateStore(paths).save(state)
        ManifestStore(paths).rewrite(records)

        handler = ScriptedHandler({SID_B: ("ok", "Mid")})
        spy = MergeSpy()

        def no_enum(*a, **k):
            raise AssertionError("enumeration must not rerun when complete")

        runner = XUserArchiveRunner(
            handler_factory=lambda handle, args: handler,
            enumerator_factory=no_enum,
            merge_func=spy,
            sleep_func=lambda d: asyncio.sleep(0),
            clock=lambda: FIXED_NOW,
        )
        args = make_args(tmp_path, resume=paths.task_id)

        code = runner.run(args)

        assert code == EXIT_SUCCESS
        assert handler.calls == [SID_B, SID_C]
        assert (done_dir / "old.html").exists()
        assert not (residue_dir / "stale.tmp").exists()
        assert len(spy.calls) == 1

        # manifest ordering unchanged and no duplicates
        rows, warnings = ManifestStore(paths).load()
        assert [r.tweet_id for r in rows] == [SID_A, SID_B, SID_C]
        assert warnings == []

    def test_resume_trusts_start_boundary_manifest_without_rescroll(
        self, tmp_path
    ):
        """partial/start_boundary (articles channel absent) is still a
        boundary-confirmed posts snapshot: resume must not re-scroll."""
        from ytbot.services.x_user_archive.manifest import TaskPaths

        archive_root = str(tmp_path / "x_archive")
        paths = TaskPaths.create(archive_root, "elonmusk", now=FIXED_NOW)
        records = [
            make_record(SID_A), make_record(SID_B), make_record(SID_C)
        ]
        state = TaskState(
            task_id=paths.task_id, author_id="elonmusk",
            start_at="2021-10-10T00:00:00+00:00",
            end_at="2026-10-10T00:00:00+00:00",
            enum_status=EnumStatus.PARTIAL,
            enum_stop_reason=EnumStopReason.START_BOUNDARY,
            unique=3,
        )
        TaskStateStore(paths).save(state)
        ManifestStore(paths).rewrite(records)

        handler = ScriptedHandler()
        spy = MergeSpy()

        def no_enum(*a, **k):
            raise AssertionError(
                "start_boundary manifest must be reused, not re-scrolled"
            )

        runner = XUserArchiveRunner(
            handler_factory=lambda handle, args: handler,
            enumerator_factory=no_enum,
            merge_func=spy,
            sleep_func=lambda d: asyncio.sleep(0),
            clock=lambda: FIXED_NOW,
        )
        args = make_args(tmp_path, resume=paths.task_id)

        code = runner.run(args)

        # enum_status remains partial (articles channel caveat) -> exit 2
        assert code == EXIT_PARTIAL
        assert handler.calls == [SID_A, SID_B, SID_C]
        assert len(spy.calls) == 1

    def test_resume_refresh_enum_forces_rescroll(self, tmp_path):
        from ytbot.services.x_user_archive.manifest import TaskPaths

        archive_root = str(tmp_path / "x_archive")
        paths = TaskPaths.create(archive_root, "elonmusk", now=FIXED_NOW)
        records = [
            make_record(SID_A), make_record(SID_B), make_record(SID_C)
        ]
        state = TaskState(
            task_id=paths.task_id, author_id="elonmusk",
            start_at="2021-10-10T00:00:00+00:00",
            end_at="2026-10-10T00:00:00+00:00",
            enum_status=EnumStatus.PARTIAL,
            enum_stop_reason=EnumStopReason.START_BOUNDARY,
            unique=3,
        )
        TaskStateStore(paths).save(state)
        ManifestStore(paths).rewrite(records)

        handler = ScriptedHandler()
        spy = MergeSpy()
        enum_calls = {"n": 0}

        def counting_factory(extractor, options, sleep_func, preload_ids):
            enum_calls["n"] += 1
            # preload must carry the stored ids even though this scroll
            # observes nothing new
            assert preload_ids == {SID_A, SID_B, SID_C}
            return FakeEnumerator([], complete_outcome(0))

        runner = XUserArchiveRunner(
            handler_factory=lambda handle, args: handler,
            enumerator_factory=counting_factory,
            merge_func=spy,
            sleep_func=lambda d: asyncio.sleep(0),
            clock=lambda: FIXED_NOW,
        )
        args = make_args(
            tmp_path, resume=paths.task_id, refresh_enum=True
        )

        code = runner.run(args)

        assert enum_calls["n"] == 1
        assert code == EXIT_SUCCESS
        assert handler.calls == [SID_A, SID_B, SID_C]
