"""Cross-task catalog and zero-copy reuse tests.

Covers:
* catalog build: snapshots, validated saved posts, stale path fallback,
  unreadable tasks, newest-wins
* window covering logic (adjacent intervals, gaps, scope supersets)
* runner: fresh task served entirely from other tasks (no browser, no
  download), live fallback, --refresh-enum, and donor-deleted resume
"""

import asyncio
import shutil
from datetime import timedelta
from pathlib import Path

from ytbot.services.x_user_archive.archiver import post_dir_name
from ytbot.services.x_user_archive.catalog import (
    build_author_catalog,
    index_path,
)
from ytbot.services.x_user_archive.manifest import (
    ManifestStore,
    TaskPaths,
    TaskState,
    TaskStateStore,
)
from ytbot.services.x_user_archive.models import (
    DownloadStatus,
    EnumStatus,
    EnumStopReason,
    snowflake_to_datetime,
)
from ytbot.services.x_user_archive.runner import (
    EXIT_SUCCESS,
    XUserArchiveRunner,
)

# Reuse the runner test fakes/helpers
from .test_x_archive_runner import (  # noqa: E402
    FIXED_NOW,
    MergeSpy,
    ScriptedHandler,
    SID_A,
    SID_B,
    SID_C,
    complete_outcome,
    FakeEnumerator,
    make_args,
    make_record,
)

UTC = FIXED_NOW.tzinfo


# ------------------------------------------------------------------ helpers

def materialize(paths: TaskPaths, record, *, with_pdf=True) -> Path:
    d = (
        paths.task_dir
        / record.published_at.strftime("%Y")
        / record.published_at.strftime("%m")
        / post_dir_name(record, record.title)
    )
    d.mkdir(parents=True, exist_ok=True)
    (d / "post.html").write_text("<html>x</html>", encoding="utf-8")
    if with_pdf:
        (d / "post.pdf").write_bytes(b"%PDF-1.4\n")
    record.archive_path = str(d)
    return d


def seed_task(
    root, handle, task_id, start, end, records, *,
    enum_status=EnumStatus.COMPLETE,
    stop_reason=EnumStopReason.START_BOUNDARY,
    include_replies=False, include_reposts=True,
):
    paths = TaskPaths(Path(root), handle, task_id)
    paths.task_dir.mkdir(parents=True)
    paths.reports_dir.mkdir(parents=True)
    state = TaskState(
        task_id=task_id, author_id=handle,
        start_at=start.isoformat(), end_at=end.isoformat(),
        enum_status=enum_status, enum_stop_reason=stop_reason,
        unique=len(records),
        options={
            "include_replies": include_replies,
            "include_reposts": include_reposts,
        },
    )
    TaskStateStore(paths).save(state)
    ManifestStore(paths).rewrite(records)
    return paths


def times_of(*ids):
    return [snowflake_to_datetime(i) for i in ids]


def make_catalog_runner(handler, spy, enum_factory):
    return XUserArchiveRunner(
        handler_factory=lambda handle, args: handler,
        enumerator_factory=enum_factory,
        merge_func=spy,
        sleep_func=lambda d: asyncio.sleep(0),
        clock=lambda: FIXED_NOW,
    )


def boom_factory(*a, **k):
    raise AssertionError("enumerator must not be created from index coverage")


# --------------------------------------------------------------- catalog

class TestAuthorCatalog:
    def test_build_indexes_snapshots_and_saved_posts(self, tmp_path):
        root = str(tmp_path / "x_archive")
        t0, _t1, t2 = times_of(SID_A, SID_B, SID_C)
        paths = seed_task(
            root, "elonmusk", "20260101T000000",
            t0 - timedelta(days=30), t2 + timedelta(days=30),
            [make_record(SID_A, DownloadStatus.DOWNLOADED, title="A"),
             make_record(SID_B, DownloadStatus.PENDING),
             make_record(SID_C, DownloadStatus.DOWNLOADED)],
        )
        materialize(paths, next(
            r for r in ManifestStore(paths).load()[0] if r.tweet_id == SID_A
        ))
        materialize(paths, next(
            r for r in ManifestStore(paths).load()[0] if r.tweet_id == SID_C
        ))
        ManifestStore(paths).rewrite(ManifestStore(paths).load()[0])

        cat = build_author_catalog(root, "elonmusk", now=FIXED_NOW)

        assert len(cat.snapshots) == 1
        assert set(cat.posts) == {SID_A, SID_C}
        # pending-only id is still observed for preload
        assert SID_B in cat.known_ids()
        assert cat.posts[SID_A].task_id == "20260101T000000"
        assert index_path(root, "elonmusk").exists()

    def test_stale_archive_path_falls_back_to_layout_scan(self, tmp_path):
        root = str(tmp_path / "x_archive")
        t0, _t1, t2 = times_of(SID_A, SID_B, SID_C)
        paths = seed_task(
            root, "elonmusk", "20260101T000000",
            t0 - timedelta(days=30), t2 + timedelta(days=30),
            [make_record(SID_A, DownloadStatus.DOWNLOADED)],
        )
        real = materialize(paths, make_record(SID_A, title="A"))
        # corrupt the manifest pointer AFTER materialization
        rows, _ = ManifestStore(paths).load()
        rows[0].archive_path = str(paths.task_dir / "nope" / "gone")
        ManifestStore(paths).rewrite(rows)

        cat = build_author_catalog(root, "elonmusk", now=FIXED_NOW)

        assert cat.posts[SID_A].archive_path == real.resolve()

    def test_invalid_product_excluded(self, tmp_path):
        root = str(tmp_path / "x_archive")
        t0, _t1, t2 = times_of(SID_A, SID_B, SID_C)
        paths = seed_task(
            root, "elonmusk", "20260101T000000",
            t0 - timedelta(days=30), t2 + timedelta(days=30),
            [make_record(SID_A, DownloadStatus.DOWNLOADED)],
        )
        d = paths.task_dir / "2022" / "01" / "broken_000123"
        d.mkdir(parents=True)
        (d / "notes.txt").write_text("no html here")
        rows = [make_record(SID_A, DownloadStatus.DOWNLOADED)]
        rows[0].archive_path = str(d)
        ManifestStore(paths).rewrite(rows)

        cat = build_author_catalog(root, "elonmusk", now=FIXED_NOW)
        assert cat.posts == {}

    def test_newest_task_wins_duplicate_product(self, tmp_path):
        root = str(tmp_path / "x_archive")
        t0, _t1, t2 = times_of(SID_A, SID_B, SID_C)
        for tid in ("20260101T000000", "20260202T000000"):
            paths = seed_task(
                root, "elonmusk", tid,
                t0 - timedelta(days=30), t2 + timedelta(days=30),
                [make_record(SID_A, DownloadStatus.DOWNLOADED)],
            )
            materialize(paths, make_record(SID_A, title=tid))
        cat = build_author_catalog(root, "elonmusk", now=FIXED_NOW)
        assert cat.posts[SID_A].task_id == "20260202T000000"


class TestWindowCovering:
    def _cat(self, root, windows, **kw):
        for i, (s, e) in enumerate(windows):
            seed_task(
                root, "elonmusk", f"2026010{i + 1}T000000", s, e, [],
                **kw,
            )
        return build_author_catalog(root, "elonmusk", now=FIXED_NOW,
                                    persist=False)

    def test_single_and_adjacent_intervals_cover(self, tmp_path):
        root = str(tmp_path / "x_archive")
        t0, t1, t2 = times_of(SID_A, SID_B, SID_C)
        cat = self._cat(root, [(t0 - timedelta(days=2), t2 + timedelta(days=2))])
        donors = cat.covering_donors(
            t0, t2 + timedelta(seconds=1),
            include_replies=False, include_reposts=True,
        )
        assert donors == ["20260101T000000"]

        root2 = str(tmp_path / "x_archive2")
        cat2 = self._cat(root2, [
            (t0 - timedelta(days=2), t1),
            (t1, t2 + timedelta(days=2)),
        ])
        donors = cat2.covering_donors(
            t0, t2 + timedelta(seconds=1),
            include_replies=False, include_reposts=True,
        )
        assert donors is not None and len(donors) == 2

    def test_gap_is_not_covered(self, tmp_path):
        root = str(tmp_path / "x_archive")
        t0, t1, t2 = times_of(SID_A, SID_B, SID_C)
        cat = self._cat(root, [
            (t0 - timedelta(days=2), t0 + timedelta(hours=1)),
            (t2 - timedelta(hours=1), t2 + timedelta(days=2)),
        ])
        assert cat.covering_donors(
            t0, t2 + timedelta(seconds=1),
            include_replies=False, include_reposts=True,
        ) is None

    def test_scope_superset_rules(self, tmp_path):
        root = str(tmp_path / "x_archive")
        t0, _t1, t2 = times_of(SID_A, SID_B, SID_C)

        # donor has no replies: cannot serve a replies request
        cat = self._cat(
            root, [(t0 - timedelta(days=2), t2 + timedelta(days=2))],
            include_replies=False,
        )
        assert cat.covering_donors(
            t0, t2 + timedelta(seconds=1),
            include_replies=True, include_reposts=True,
        ) is None
        assert cat.covering_donors(
            t0, t2 + timedelta(seconds=1),
            include_replies=False, include_reposts=True,
        ) is not None

        # donor excluded reposts: cannot serve a reposts request
        root2 = str(tmp_path / "x_archive2")
        cat2 = self._cat(
            root2, [(t0 - timedelta(days=2), t2 + timedelta(days=2))],
            include_reposts=False,
        )
        assert cat2.covering_donors(
            t0, t2 + timedelta(seconds=1),
            include_replies=False, include_reposts=True,
        ) is None
        assert cat2.covering_donors(
            t0, t2 + timedelta(seconds=1),
            include_replies=False, include_reposts=False,
        ) is not None

    def test_stall_and_max_posts_snapshots_never_cover(self, tmp_path):
        t0, _t1, t2 = times_of(SID_A, SID_B, SID_C)
        for stop in (EnumStopReason.STALL, EnumStopReason.MAX_POSTS):
            r = str(tmp_path / f"x_{stop}")
            seed_task(
                r, "elonmusk", "20260101T000000",
                t0 - timedelta(days=2), t2 + timedelta(days=2), [],
                enum_status=EnumStatus.PARTIAL, stop_reason=stop,
            )
            cat = build_author_catalog(r, "elonmusk", now=FIXED_NOW,
                                       persist=False)
            assert cat.covering_donors(
                t0, t2 + timedelta(seconds=1),
                include_replies=False, include_reposts=True,
            ) is None


# ------------------------------------------------------- runner reuse

class TestRunnerCrossTaskReuse:
    def _donor(self, root):
        t0, _t1, t2 = times_of(SID_A, SID_B, SID_C)
        records = [
            make_record(SID_A, DownloadStatus.DOWNLOADED, title="AAA"),
            make_record(SID_B, DownloadStatus.DOWNLOADED, title="BBB"),
            make_record(SID_C, DownloadStatus.DOWNLOADED, title="CCC"),
        ]
        paths = seed_task(
            root, "elonmusk", "20260101T000000",
            t0 - timedelta(days=30), t2 + timedelta(days=30),
            records,
        )
        for rec in ManifestStore(paths).load()[0]:
            materialize(paths, rec)
        ManifestStore(paths).rewrite(ManifestStore(paths).load()[0])
        return paths, (t0, t2)

    def test_covered_task_skips_browser_and_downloads(self, tmp_path, capsys):
        root = str(tmp_path / "x_archive")
        _donor_paths, (t0, t2) = self._donor(root)
        handler = ScriptedHandler()
        spy = MergeSpy()
        runner = make_catalog_runner(handler, spy, boom_factory)
        args = make_args(
            tmp_path,
            start_at=(t0 - timedelta(days=1)).isoformat(),
            end_at=(t2 + timedelta(days=1)).isoformat(),
        )

        code = runner.run(args)
        out = capsys.readouterr().out

        assert code == EXIT_SUCCESS
        assert handler.calls == []
        assert "index_coverage" in out
        assert "正文复用历史存档 3 篇" in out
        kwargs = spy.calls[0]
        assert {r.tweet_id for r in kwargs["records"]} == {
            SID_A, SID_B, SID_C
        }
        for rec in kwargs["records"]:
            assert rec.status == DownloadStatus.SKIPPED
            assert rec.reused_from == "20260101T000000"
            assert Path(rec.archive_path).is_dir()
        new_task_dir = next(
            p for p in (Path(root) / "elonmusk" / "tasks").iterdir()
            if p.name != "20260101T000000"
        )
        import json
        report = json.loads(
            (new_task_dir / "reports" / f"{new_task_dir.name}.json")
            .read_text()
        )
        assert report["enum_source"] == "index"
        assert report["reused_posts"] == 3
        assert report["enum_donor_task_ids"] == ["20260101T000000"]

    def test_uncovered_window_runs_live_with_catalog_preload(
        self, tmp_path
    ):
        root = str(tmp_path / "x_archive")
        self._donor(root)
        seen = {}

        def factory(extractor, options, sleep_func, preload_ids):
            seen["preload"] = set(preload_ids)
            return FakeEnumerator([], complete_outcome(0))

        handler = ScriptedHandler()
        spy = MergeSpy()
        runner = make_catalog_runner(handler, spy, factory)
        # window far AFTER the donor window -> not covered
        _t0, _t1, t2 = times_of(SID_A, SID_B, SID_C)
        args = make_args(
            tmp_path,
            start_at=(t2 + timedelta(days=40)).isoformat(),
            end_at=(t2 + timedelta(days=80)).isoformat(),
        )

        runner.run(args)

        assert {SID_A, SID_B, SID_C} <= seen["preload"]

    def test_refresh_enum_forces_live_scroll(self, tmp_path):
        root = str(tmp_path / "x_archive")
        _paths, (t0, t2) = self._donor(root)
        calls = {"n": 0}

        def factory(extractor, options, sleep_func, preload_ids):
            calls["n"] += 1
            return FakeEnumerator([], complete_outcome(0))

        runner = make_catalog_runner(ScriptedHandler(), MergeSpy(), factory)
        args = make_args(
            tmp_path,
            start_at=(t0 - timedelta(days=1)).isoformat(),
            end_at=(t2 + timedelta(days=1)).isoformat(),
            refresh_enum=True,
        )

        runner.run(args)
        assert calls["n"] == 1

    def test_resume_after_donor_deletion_redownloads(self, tmp_path):
        root = str(tmp_path / "x_archive")
        donor_paths, (t0, t2) = self._donor(root)
        spy = MergeSpy()
        handler = ScriptedHandler()
        runner = make_catalog_runner(handler, spy, boom_factory)
        args = make_args(
            tmp_path,
            start_at=(t0 - timedelta(days=1)).isoformat(),
            end_at=(t2 + timedelta(days=1)).isoformat(),
        )
        assert runner.run(args) == EXIT_SUCCESS
        assert handler.calls == []

        # donor archive disappears (author moved/deleted old task)
        shutil.rmtree(donor_paths.task_dir)

        new_task_id = next(
            p.name for p in (Path(root) / "elonmusk" / "tasks").iterdir()
            if p.name != "20260101T000000"
        )
        handler2 = ScriptedHandler()
        spy2 = MergeSpy()
        runner2 = make_catalog_runner(handler2, spy2, boom_factory)
        args2 = make_args(tmp_path, resume=new_task_id)

        code = runner2.run(args2)

        assert code == EXIT_SUCCESS
        assert sorted(handler2.calls) == sorted([SID_A, SID_B, SID_C])
        # products now live inside the current task dir
        rows, _ = ManifestStore(
            TaskPaths(Path(root), "elonmusk", new_task_id)
        ).load()
        for rec in rows:
            assert rec.status == DownloadStatus.DOWNLOADED
            assert str(
                TaskPaths(Path(root), "elonmusk", new_task_id).task_dir
            ) in rec.archive_path
