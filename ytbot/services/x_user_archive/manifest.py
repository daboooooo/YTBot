"""Manifest / task-state persistence for X user archive.

Layout (spec sections 11.1, 13; the spec's resume example pins the
``tasks/<task_id>`` directory, so all task-scoped files live there):

::

    <archive-dir>/<handle>/
        <handle>_<startYYYYMMDD>-<endYYYYMMDD>.pdf   # merged PDF (stable)
        tasks/<task_id>/
            task_state.json
            manifest.jsonl
            reports/<task_id>.json
            <YYYY>/<MM>/<YYYY-MM-DD_title_shortid>/  # per-post content

JSONL rows are appended during enumeration; a merged, sorted rewrite
happens once enumeration finishes and after each status update.
JSON state files use temp-file + ``os.replace`` atomic replacement.
"""

import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .models import ContentRecord

TASK_STATE_NAME = "task_state.json"
MANIFEST_NAME = "manifest.jsonl"
REPORTS_DIRNAME = "reports"
TASKS_DIRNAME = "tasks"


# ---------------------------------------------------------------------------
# Atomic file IO
# ---------------------------------------------------------------------------

def atomic_write_text(path: Path, text: str) -> None:
    """Write text via same-dir temp file + os.replace (fsync'd)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Task paths
# ---------------------------------------------------------------------------

class TaskPathsError(Exception):
    pass


@dataclass
class TaskPaths:
    archive_root: Path
    handle: str
    task_id: str

    @property
    def handle_dir(self) -> Path:
        return Path(self.archive_root) / self.handle

    @property
    def task_dir(self) -> Path:
        return self.handle_dir / TASKS_DIRNAME / self.task_id

    @property
    def state_path(self) -> Path:
        return self.task_dir / TASK_STATE_NAME

    @property
    def manifest_path(self) -> Path:
        return self.task_dir / MANIFEST_NAME

    @property
    def reports_dir(self) -> Path:
        return self.task_dir / REPORTS_DIRNAME

    @property
    def content_root(self) -> Path:
        return self.task_dir

    def report_path(self, task_id: Optional[str] = None) -> Path:
        return self.reports_dir / f"{task_id or self.task_id}.json"

    def default_merged_pdf_path(self, start: datetime, end: datetime) -> Path:
        name = (
            f"{self.handle}_"
            f"{start.strftime('%Y%m%d')}-{end.strftime('%Y%m%d')}.pdf"
        )
        return self.handle_dir / name

    @classmethod
    def create(
        cls,
        archive_root: str,
        handle: str,
        now: Optional[datetime] = None,
    ) -> "TaskPaths":
        now = now or datetime.now(timezone.utc)
        base = cls(Path(archive_root), handle, now.strftime("%Y%m%dT%H%M%S"))
        # Deterministic collision handling: same-second reruns get -2/-3
        candidate = base
        suffix = 1
        while candidate.state_path.exists():
            suffix += 1
            candidate = cls(
                Path(archive_root), handle,
                f"{base.task_id}-{suffix}",
            )
        candidate.task_dir.mkdir(parents=True, exist_ok=False)
        candidate.reports_dir.mkdir(parents=True, exist_ok=True)
        return candidate

    @classmethod
    def resume(cls, path_or_id: str, archive_root: str) -> "TaskPaths":
        """Resolve --resume: task directory, or task_id under archive root."""
        candidate = Path(path_or_id).expanduser()
        if candidate.is_dir():
            state = candidate / TASK_STATE_NAME
            if not state.exists():
                raise TaskPathsError(
                    f"resume directory has no {TASK_STATE_NAME}: {candidate}"
                )
            task_id = candidate.name
            handle = candidate.parent.parent.name
            if candidate.parent.name != TASKS_DIRNAME:
                raise TaskPathsError(
                    f"unexpected task directory layout: {candidate}"
                )
            return cls(Path(archive_root), handle, task_id)

        # Treat as task_id: glob <root>/<handle>/tasks/<id>
        hits = list(
            Path(archive_root).glob(f"*/{TASKS_DIRNAME}/{path_or_id}")
        )
        if not hits:
            raise TaskPathsError(
                f"cannot find task to resume: {path_or_id}"
            )
        if len(hits) > 1:
            raise TaskPathsError(
                f"ambiguous task id {path_or_id!r}: {hits}"
            )
        task_dir = hits[0]
        return cls(Path(archive_root), task_dir.parent.parent.name,
                   task_dir.name)


# ---------------------------------------------------------------------------
# Task state (spec 13.1)
# ---------------------------------------------------------------------------

def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_merged_pdf_state() -> Dict[str, Any]:
    return {
        "status": "not_generated",
        "path": None,
        "included_posts": 0,
        "downloadable_posts": 0,
        "skipped_no_pdf": 0,
        "skipped_invalid": 0,
        "pages": 0,
        "size_bytes": 0,
        "updated_at": None,
        "error": None,
    }


@dataclass
class TaskState:
    task_id: str
    author_id: str
    start_at: str
    end_at: str
    display_name: Optional[str] = None
    options: Dict[str, Any] = field(default_factory=dict)
    enum_status: str = "unknown"
    enum_stop_reason: Optional[str] = None
    download_status: str = "pending"  # pending/running/paused/completed/failed
    # counters
    raw_discovered: int = 0
    unique: int = 0
    out_of_scope: int = 0
    out_of_range: int = 0
    invalid: int = 0
    downloaded: int = 0
    skipped: int = 0
    retryable_failed: int = 0
    permanent_failed: int = 0
    retries: int = 0
    merged_pdf: Dict[str, Any] = field(default_factory=default_merged_pdf_state)
    created_at: str = field(default_factory=_utc_now_iso)
    updated_at: str = field(default_factory=_utc_now_iso)
    last_error: Optional[str] = None

    @property
    def total(self) -> int:
        return self.unique

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TaskState":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        filtered = {k: v for k, v in data.items() if k in known}
        state = cls(**filtered)
        stored_pdf = data.get("merged_pdf")
        if isinstance(stored_pdf, dict):
            merged = default_merged_pdf_state()
            merged.update(stored_pdf)
            state.merged_pdf = merged
        return state

    def touch(self) -> None:
        self.updated_at = _utc_now_iso()


class TaskStateStore:
    def __init__(self, paths: TaskPaths):
        self.paths = paths

    def save(self, state: TaskState) -> None:
        state.touch()
        atomic_write_text(
            self.paths.state_path,
            json.dumps(state.to_dict(), ensure_ascii=False, indent=2),
        )

    def load(self) -> TaskState:
        with open(self.paths.state_path, "r", encoding="utf-8") as fh:
            return TaskState.from_dict(json.load(fh))


# ---------------------------------------------------------------------------
# Manifest JSONL
# ---------------------------------------------------------------------------

def sort_key(record: ContentRecord) -> Tuple[datetime, int]:
    return record.published_at, int(record.tweet_id)


def dedupe_merge(
    records: List[ContentRecord],
) -> Tuple[List[ContentRecord], List[str]]:
    """Merge duplicate (author_id, tweet_id) observations.

    Boolean flags OR together; unknown content_type is upgraded;
    conflicting identity values produce diagnostics. First observation
    wins for timestamps/URLs.
    """
    by_key: Dict[Tuple[str, str], ContentRecord] = {}
    diagnostics: List[str] = []
    for rec in records:
        existing = by_key.get(rec.key)
        if existing is None:
            by_key[rec.key] = rec
            continue
        for flag in ("is_reply", "is_repost", "is_pinned"):
            if getattr(rec, flag):
                setattr(existing, flag, True)
        if existing.content_type == "unknown" and rec.content_type != "unknown":
            existing.content_type = rec.content_type
        if not existing.dom_datetime and rec.dom_datetime:
            existing.dom_datetime = rec.dom_datetime
        if not existing.title and rec.title:
            existing.title = rec.title
        if existing.status_author != rec.status_author:
            diagnostics.append(
                f"status_author conflict for {rec.key}: "
                f"{existing.status_author} vs {rec.status_author}"
            )
    merged = sorted(by_key.values(), key=sort_key)
    return merged, diagnostics


class ManifestStore:
    def __init__(self, paths: TaskPaths):
        self.paths = paths

    def append_batch(self, records: List[ContentRecord]) -> None:
        """Append raw observations (JSONL, one batch per call)."""
        if not records:
            return
        lines = "".join(
            json.dumps(r.to_dict(), ensure_ascii=False) + "\n"
            for r in records
        )
        self.paths.task_dir.mkdir(parents=True, exist_ok=True)
        with open(self.paths.manifest_path, "a", encoding="utf-8") as fh:
            fh.write(lines)
            fh.flush()
            os.fsync(fh.fileno())

    def load(self) -> Tuple[List[ContentRecord], List[str]]:
        """Read all rows; corrupt lines are skipped with warnings."""
        records: List[ContentRecord] = []
        warnings: List[str] = []
        path = self.paths.manifest_path
        if not path.exists():
            return records, warnings
        with open(path, "r", encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(ContentRecord.from_dict(json.loads(line)))
                except (ValueError, KeyError, json.JSONDecodeError) as exc:
                    warnings.append(
                        f"manifest line {lineno} skipped: "
                        f"{type(exc).__name__}: {exc}"
                    )
        return records, warnings

    def rewrite(self, records: List[ContentRecord]) -> None:
        """Atomically persist the canonical sorted manifest."""
        ordered = sorted(records, key=sort_key)
        text = "".join(
            json.dumps(r.to_dict(), ensure_ascii=False) + "\n"
            for r in ordered
        )
        atomic_write_text(self.paths.manifest_path, text)
