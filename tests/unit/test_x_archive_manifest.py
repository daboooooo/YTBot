"""Unit tests for x_user_archive manifest / task-state persistence."""

import json
from datetime import datetime, timezone

import pytest

from ytbot.services.x_user_archive.manifest import (
    ManifestStore,
    TaskPaths,
    TaskPathsError,
    TaskState,
    TaskStateStore,
    dedupe_merge,
)
from ytbot.services.x_user_archive.models import (
    TWITTER_SNOWFLAKE_EPOCH_MS,
    DownloadStatus,
    build_record,
)

UTC = timezone.utc


def _id(ms: int, seq: int = 0) -> str:
    return str(((ms - TWITTER_SNOWFLAKE_EPOCH_MS) << 22) | seq)


@pytest.fixture
def archive_root(tmp_path):
    return tmp_path / "x_archive"


class TestTaskPaths:
    def test_create_layout(self, archive_root):
        now = datetime(2026, 10, 10, 15, 30, 0, tzinfo=UTC)
        paths = TaskPaths.create(str(archive_root), "elonmusk", now=now)
        assert paths.task_id == "20261010T153000"
        assert paths.task_dir == (
            archive_root / "elonmusk" / "tasks" / "20261010T153000"
        )
        assert paths.reports_dir.is_dir()
        assert paths.content_root == paths.task_dir

    def test_same_second_collision_gets_suffix(self, archive_root):
        now = datetime(2026, 10, 10, 15, 30, 0, tzinfo=UTC)
        p1 = TaskPaths.create(str(archive_root), "elonmusk", now=now)
        # mark state as existing for p1 to simulate a finished creation
        p1.state_path.write_text("{}", encoding="utf-8")
        p2 = TaskPaths.create(str(archive_root), "elonmusk", now=now)
        assert p2.task_id == "20261010T153000-2"

    def test_default_merged_pdf_name(self, archive_root):
        now = datetime(2026, 10, 10, 15, 30, tzinfo=UTC)
        paths = TaskPaths.create(str(archive_root), "elonmusk", now=now)
        pdf = paths.default_merged_pdf_path(
            datetime(2021, 10, 10, tzinfo=UTC), datetime(2026, 10, 10, tzinfo=UTC)
        )
        assert pdf == archive_root / "elonmusk" / (
            "elonmusk_20211010-20261010.pdf"
        )

    def test_resume_by_directory(self, archive_root):
        now = datetime(2026, 10, 10, 15, 30, 0, tzinfo=UTC)
        paths = TaskPaths.create(str(archive_root), "elonmusk", now=now)
        TaskStateStore(paths).save(TaskState(
            task_id=paths.task_id, author_id="elonmusk",
            start_at="2021-10-10T00:00:00+00:00",
            end_at="2026-10-10T00:00:00+00:00",
        ))
        resumed = TaskPaths.resume(str(paths.task_dir), str(archive_root))
        assert resumed.task_id == paths.task_id
        assert resumed.handle == "elonmusk"

    def test_resume_by_task_id_glob(self, archive_root):
        now = datetime(2026, 10, 10, 15, 30, tzinfo=UTC)
        paths = TaskPaths.create(str(archive_root), "elonmusk", now=now)
        paths.state_path.write_text("{}", encoding="utf-8")
        resumed = TaskPaths.resume(paths.task_id, str(archive_root))
        assert resumed.task_dir == paths.task_dir

    def test_resume_missing_raises(self, archive_root):
        with pytest.raises(TaskPathsError):
            TaskPaths.resume("20990101T000000", str(archive_root))

    def test_resume_dir_without_state_raises(self, tmp_path):
        bad = tmp_path / "tasks" / "20261010T153000"
        bad.mkdir(parents=True)
        with pytest.raises(TaskPathsError):
            TaskPaths.resume(str(bad), str(tmp_path))


class TestTaskStateStore:
    def test_save_and_load_roundtrip(self, archive_root):
        paths = TaskPaths.create(str(archive_root), "elonmusk")
        state = TaskState(
            task_id=paths.task_id,
            author_id="elonmusk",
            start_at="2021-10-10T00:00:00+00:00",
            end_at="2026-10-10T00:00:00+00:00",
            options={"max_posts": 1000, "include_replies": False},
            enum_status="partial",
        )
        TaskStateStore(paths).save(state)
        loaded = TaskStateStore(paths).load()
        assert loaded.task_id == state.task_id
        assert loaded.options["max_posts"] == 1000
        assert loaded.merged_pdf["status"] == "not_generated"
        assert loaded.merged_pdf["pages"] == 0

    def test_unknown_fields_ignored_on_load(self, archive_root):
        paths = TaskPaths.create(str(archive_root), "elonmusk")
        state_path = paths.state_path
        state_path.write_text(
            json.dumps({
                "task_id": "t", "author_id": "elonmusk",
                "start_at": "2021-01-01T00:00:00+00:00",
                "end_at": "2026-01-01T00:00:00+00:00",
                "future_field": "whatever",
            }),
            encoding="utf-8",
        )
        loaded = TaskStateStore(paths).load()
        assert loaded.author_id == "elonmusk"
        assert not hasattr(loaded, "future_field")

    def test_save_is_atomic_no_tmp_left(self, archive_root):
        paths = TaskPaths.create(str(archive_root), "elonmusk")
        store = TaskStateStore(paths)
        store.save(TaskState(
            task_id="t", author_id="elonmusk",
            start_at="2021-01-01T00:00:00+00:00",
            end_at="2026-01-01T00:00:00+00:00",
        ))
        leftovers = [
            p.name for p in paths.task_dir.iterdir()
            if p.name.startswith(".") and p.name.endswith(".tmp")
        ]
        assert leftovers == []


class TestManifestStore:
    @pytest.fixture
    def store(self, archive_root):
        paths = TaskPaths.create(str(archive_root), "elonmusk")
        return ManifestStore(paths), paths

    def test_append_load_rewrite_cycle(self, store):
        manifest, paths = store
        r1 = build_record(_id(1500000000000), "elonmusk")
        r2 = build_record(_id(1700000000000), "elonmusk")
        manifest.append_batch([r2])
        manifest.append_batch([r1, r2])  # dup appended raw
        loaded, warnings = manifest.load()
        assert warnings == []
        assert len(loaded) == 3
        merged, diags = dedupe_merge(loaded)
        assert diags == []
        assert [r.tweet_id for r in merged] == [r1.tweet_id, r2.tweet_id]
        manifest.rewrite(merged)
        again, warnings = manifest.load()
        assert warnings == []
        assert [r.tweet_id for r in again] == [r1.tweet_id, r2.tweet_id]

    def test_corrupt_lines_skipped_with_warning(self, store):
        manifest, paths = store
        good = build_record(_id(1700000000000), "elonmusk")
        manifest.append_batch([good])
        with open(paths.manifest_path, "a", encoding="utf-8") as fh:
            fh.write('{"tweet_id": "broken",\n')   # truncated/invalid tail
            fh.write("\n")                          # blank line tolerated
        loaded, warnings = manifest.load()
        assert len(loaded) == 1
        assert len(warnings) == 1
        assert "line 2" in warnings[0]

    def test_load_missing_file_empty(self, store):
        manifest, paths = store
        path = paths.manifest_path
        if path.exists():
            path.unlink()
        loaded, warnings = manifest.load()
        assert loaded == [] and warnings == []

    def test_download_status_persists(self, store):
        manifest, _ = store
        rec = build_record(_id(1700000000000), "elonmusk")
        rec.status = DownloadStatus.DOWNLOADED
        rec.archive_path = "2023/11/x"
        manifest.rewrite([rec])
        loaded, _ = manifest.load()
        assert loaded[0].status == DownloadStatus.DOWNLOADED
        assert loaded[0].archive_path == "2023/11/x"
