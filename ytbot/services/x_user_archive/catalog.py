"""Author-level cross-task index (enumeration snapshots + saved posts).

Historical posts are immutable unless the author deletes them, so every
trustworthy enumeration and every validated per-post product under
``<archive-dir>/<handle>/tasks/`` is reusable by later tasks — even tasks
with narrower windows. This module builds that reusable view.

The index is a *derived* cache, rebuilt by scanning the task manifests on
every run; manifests stay the source of truth, so there is no
dual-write consistency problem. The persisted JSON lets humans inspect
what the runner saw and keeps future warm-start options open.

Layout::

    <archive-dir>/<handle>/index/post_index.json

Zero-copy reuse policy: the index only records posts whose product
directory currently passes ``is_valid_content_dir``; consumers point at
the donor task directory directly (no files are copied).
"""

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set

from ...core.enhanced_logger import get_logger
from .archiver import is_valid_content_dir
from .manifest import (
    TASKS_DIRNAME,
    ManifestStore,
    TaskPaths,
    TaskState,
    TaskStateStore,
    atomic_write_text,
)
from .models import (
    ContentRecord,
    EnumStatus,
    EnumStopReason,
    parse_iso_utc,
)

logger = get_logger(__name__)

INDEX_DIRNAME = "index"
INDEX_FILENAME = "post_index.json"
INDEX_VERSION = 1


@dataclass
class SnapshotInfo:
    """One task's enumeration coverage."""

    task_id: str
    start_at: datetime
    end_at: datetime
    include_replies: bool
    include_reposts: bool
    enum_status: str
    stop_reason: Optional[str]

    @property
    def trustworthy(self) -> bool:
        """Coverage of posts is confirmed only for clean/boundary stops.

        ``max_posts`` truncation and ``stall`` may hide older posts.
        """
        return (
            self.enum_status == EnumStatus.COMPLETE
            or self.stop_reason == EnumStopReason.START_BOUNDARY
        )

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "start_at": self.start_at.isoformat(),
            "end_at": self.end_at.isoformat(),
            "include_replies": self.include_replies,
            "include_reposts": self.include_reposts,
            "enum_status": self.enum_status,
            "stop_reason": self.stop_reason,
        }


@dataclass
class SavedPost:
    """A validated per-post product owned by some task."""

    tweet_id: str
    published_at: datetime
    is_reply: bool
    is_repost: bool
    content_type: str
    title: Optional[str]
    task_id: str
    archive_path: Path

    def to_dict(self) -> dict:
        return {
            "tweet_id": self.tweet_id,
            "published_at": self.published_at.isoformat(),
            "is_reply": self.is_reply,
            "is_repost": self.is_repost,
            "content_type": self.content_type,
            "title": self.title,
            "task_id": self.task_id,
            "archive_path": str(self.archive_path),
        }


@dataclass
class AuthorCatalog:
    handle: str
    snapshots: List[SnapshotInfo] = field(default_factory=list)
    posts: Dict[str, SavedPost] = field(default_factory=dict)
    # Every id ever observed (even without a saved product); used to
    # preload a live enumeration so rescrolls stay cheap.
    observed_ids: Set[str] = field(default_factory=set)
    built_at: Optional[datetime] = None

    # -- queries ----------------------------------------------------------

    def eligible_snapshots(
        self,
        start: datetime,
        end: datetime,
        *,
        include_replies: bool,
        include_reposts: bool,
    ) -> List[SnapshotInfo]:
        """Trustworthy snapshots whose collection scope is a superset and
        whose window intersects the requested one."""
        out = []
        for snap in self.snapshots:
            if not snap.trustworthy:
                continue
            # Subset requests only: a no-replies/no-reposts snapshot can
            # never serve a broader request.
            if include_replies and not snap.include_replies:
                continue
            if include_reposts and not snap.include_reposts:
                continue
            if max(start, snap.start_at) < min(end, snap.end_at):
                out.append(snap)
        return out

    def covering_donors(
        self,
        start: datetime,
        end: datetime,
        *,
        include_replies: bool,
        include_reposts: bool,
    ) -> Optional[List[str]]:
        """Return donor task ids whose windows continuously cover
        ``[start, end)``; None when coverage has any gap."""
        intervals = sorted(
            (max(start, s.start_at), min(end, s.end_at), s.task_id)
            for s in self.eligible_snapshots(
                start, end,
                include_replies=include_replies,
                include_reposts=include_reposts,
            )
        )
        if not intervals or intervals[0][0] > start:
            return None
        donors: List[str] = []
        cursor = start
        i = 0
        while cursor < end and i < len(intervals):
            # Pick the interval starting no later than the cursor that
            # reaches furthest (greedy interval covering).
            best = None
            while i < len(intervals) and intervals[i][0] <= cursor:
                if best is None or intervals[i][1] > best[1]:
                    best = intervals[i]
                i += 1
            if best is None or best[1] <= cursor:
                return None
            if best[2] not in donors:
                donors.append(best[2])
            cursor = best[1]
        if cursor < end:
            return None
        return donors

    def known_ids(self) -> set:
        """Every tweet id this author's tasks have ever observed, including
        posts without a saved product (used to preload enumeration)."""
        return set(self.observed_ids) | set(self.posts.keys())

    def saved(self, tweet_id: str) -> Optional[SavedPost]:
        return self.posts.get(str(tweet_id))

    def to_dict(self) -> dict:
        return {
            "version": INDEX_VERSION,
            "handle": self.handle,
            "built_at": (
                self.built_at.isoformat() if self.built_at else None
            ),
            "snapshots": [s.to_dict() for s in self.snapshots],
            "posts": {
                tid: p.to_dict() for tid, p in sorted(self.posts.items())
            },
        }


def index_path(archive_root, handle) -> Path:
    return (
        Path(archive_root) / handle / INDEX_DIRNAME / INDEX_FILENAME
    )


def _resolve_product(
    record: ContentRecord, task_dir: Path
) -> Optional[Path]:
    """Validate the stored archive_path; relocate it within task_dir via
    the deterministic ``<YYYY>/<MM>/*_<short_id>`` layout if stale."""
    stored = record.archive_path
    if stored:
        candidate = Path(stored)
        if is_valid_content_dir(candidate):
            return candidate
    short = record.tweet_id[-6:]
    ym = task_dir / record.published_at.strftime("%Y") \
        / record.published_at.strftime("%m")
    if ym.is_dir():
        hits = sorted(
            p for p in ym.glob(f"*_{short}")
            if p.is_dir() and is_valid_content_dir(p)
        )
        if hits:
            return hits[0]
    return None


def load_task_records(
    archive_root, handle: str, task_id: str
) -> List[ContentRecord]:
    paths = TaskPaths(Path(archive_root), handle, task_id)
    records, warnings = ManifestStore(paths).load()
    if warnings:
        logger.warning(
            "catalog: manifest notes in %s: %s", task_id, warnings
        )
    return records


def build_author_catalog(
    archive_root,
    handle: str,
    *,
    now: Optional[datetime] = None,
    exclude_task_id: Optional[str] = None,
    persist: bool = True,
) -> AuthorCatalog:
    """Scan ``<root>/<handle>/tasks/*`` and rebuild the author index.

    Missing/corrupt tasks are skipped with warnings rather than failing
    the run (the index is an optimization layer).
    """
    catalog = AuthorCatalog(
        handle=handle, built_at=now or datetime.now(timezone.utc)
    )
    tasks_dir = Path(archive_root) / handle / TASKS_DIRNAME
    task_dirs = (
        sorted((p for p in tasks_dir.iterdir() if p.is_dir()), reverse=True)
        if tasks_dir.is_dir() else []
    )
    for task_dir in task_dirs:
        task_id = task_dir.name
        if task_id == exclude_task_id:
            continue
        paths = TaskPaths(Path(archive_root), handle, task_id)
        state_path = paths.state_path
        if not state_path.exists():
            continue
        try:
            state: TaskState = TaskStateStore(paths).load()
            records, _ = ManifestStore(paths).load()
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
            logger.warning("catalog: skipping unreadable task %s: %s",
                           task_id, exc)
            continue
        options = state.options or {}
        catalog.snapshots.append(
            SnapshotInfo(
                task_id=task_id,
                start_at=parse_iso_utc(state.start_at),
                end_at=parse_iso_utc(state.end_at),
                include_replies=bool(options.get("include_replies", False)),
                include_reposts=bool(options.get("include_reposts", True)),
                enum_status=state.enum_status,
                stop_reason=state.enum_stop_reason,
            )
        )
        # Newest task wins for duplicate products (task_dirs are sorted
        # newest-first); only DOWNLOADED rows own a product.
        for rec in records:
            catalog.observed_ids.add(rec.tweet_id)
            if rec.status != "downloaded" or rec.tweet_id in catalog.posts:
                continue
            product = _resolve_product(rec, task_dir)
            if product is None:
                continue
            catalog.posts[rec.tweet_id] = SavedPost(
                tweet_id=rec.tweet_id,
                published_at=rec.published_at,
                is_reply=rec.is_reply,
                is_repost=rec.is_repost,
                content_type=rec.content_type,
                title=rec.title,
                task_id=task_id,
                archive_path=product.resolve(),
            )

    if persist:
        try:
            path = index_path(archive_root, handle)
            atomic_write_text(
                path,
                json.dumps(catalog.to_dict(), ensure_ascii=False, indent=2),
            )
        except OSError as exc:
            logger.warning("catalog: cannot persist index: %s", exc)
    return catalog
