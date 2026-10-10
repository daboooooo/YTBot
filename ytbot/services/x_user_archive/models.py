"""Domain model for X user archive: records, windows, snowflake time.

Aligned with ``TwitterContentExtractor.scrape_tweet`` output; see spec
section 7 (docs/get_all_post.md).

Phase-1 probe evidence (dev/x_archive_phase1_findings.json):

* status snowflake ids match DOM ``time[datetime]`` within 1s over 344
  real samples, so snowflake-derived time is the authoritative clock.
* On the Posts tab a repost entry links to the ORIGINAL author
  (``/<other>/status/<id>``) with social-context label
  ``"<display_name> 已转帖"`` / "reposted". Such entries stay in scope
  (they are actions of the target user); ``status_author`` records the
  link author so ``post_url`` stays downloadable.
* ``/with_replies`` interleaves other people's conversation-context
  tweets with no marker at all (16/35 in the probe). They are filtered
  as ``out_of_scope`` before entering the manifest.
"""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

# Twitter snowflake epoch (2010-11-04T01:42:54.657Z)
TWITTER_SNOWFLAKE_EPOCH_MS = 1288834974657

HANDLE_RE = re.compile(r"^@?([A-Za-z0-9_]{1,15})$")
TWEET_ID_RE = re.compile(r"^\d+$")
_STATUS_URL_RE = re.compile(
    r"^https?://(?:x\.com|twitter\.com|mobile\.twitter\.com)"
    r"/([A-Za-z0-9_]{1,15})/status/(\d+)(?:[/?#].*)?$"
)
_PROFILE_URL_RE = re.compile(
    r"^https?://(?:x\.com|twitter\.com|mobile\.twitter\.com)"
    r"/@?([A-Za-z0-9_]{1,15})(?:[/?#].*)?$"
)
# X long-form links observed/assumed shapes (Articles channel itself is
# NOT guaranteed available — verified absent for the probe account).
ARTICLE_URL_RE = re.compile(r"/(?:i/)?article/")


class ContentKind:
    """Enumeration-stage content type (refined post-download)."""

    POST = "post"
    ARTICLE = "article"
    UNKNOWN = "unknown"


class DownloadStatus:
    PENDING = "pending"
    DOWNLOADING = "downloading"
    DOWNLOADED = "downloaded"
    SKIPPED = "skipped"
    RETRYABLE_ERROR = "retryable_error"
    PERMANENT_ERROR = "permanent_error"


class EnumStatus:
    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"
    UNKNOWN = "unknown"


class EnumStopReason:
    START_BOUNDARY = "start_boundary"        # reached entries older than window
    STALL = "stall"                          # no new ids for N rounds
    MAX_POSTS = "max_posts_reached"
    LOGIN_WALL = "login_wall"
    RATE_LIMITED = "rate_limited"
    PAGE_ERROR_LIMIT = "page_error_limit"
    INTERRUPTED = "interrupted"
    UNKNOWN = "unknown"


class SkipReason:
    OUT_OF_SCOPE = "out_of_scope"
    NO_REPOSTS = "no_reposts"


class MergedPdfStatus:
    NOT_GENERATED = "not_generated"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"      # e.g. pypdf missing / converter unavailable
    DISABLED = "disabled"    # --no-merged-pdf


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------

def snowflake_to_datetime(tweet_id) -> datetime:
    """Derive publish time (UTC, tz-aware) from a status snowflake id."""
    if isinstance(tweet_id, bool):
        raise ValueError(f"invalid tweet id: {tweet_id!r}")
    s = str(tweet_id).strip()
    if not TWEET_ID_RE.match(s):
        raise ValueError(f"invalid tweet id: {tweet_id!r}")
    value = int(s)
    if value <= 0:
        raise ValueError(f"invalid tweet id: {tweet_id!r}")
    ms = (value >> 22) + TWITTER_SNOWFLAKE_EPOCH_MS
    dt = datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)
    # Sanity: real snowflakes cannot predate Twitter or lie in the future
    # beyond a tiny clock-skew grace.
    if dt.year < 2006:
        raise ValueError(f"snowflake time out of range: {dt.isoformat()}")
    if dt > datetime.now(timezone.utc) + timedelta(days=1):
        raise ValueError(f"snowflake time in the future: {dt.isoformat()}")
    return dt


def parse_iso_utc(value) -> datetime:
    """Parse ISO-8601 to tz-aware UTC; naive input is assumed UTC."""
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _calendar_subtract_years(dt: datetime, years: int) -> datetime:
    """Subtract calendar years; Feb 29 falls back to Feb 28."""
    target_year = dt.year - years
    try:
        return dt.replace(year=target_year)
    except ValueError:
        return dt.replace(year=target_year, day=28)


def resolve_time_window(
    now: datetime,
    history_years: int = 5,
    start_at: Optional[str] = None,
    end_at: Optional[str] = None,
) -> Tuple[datetime, datetime]:
    """Fix the task window ``[start, end)`` in UTC.

    ``start_at`` overrides ``history_years``; default end is task start.
    Raises ValueError when the window is empty or inputs are invalid.
    """
    if history_years < 0:
        raise ValueError("history_years must be >= 0")
    now = parse_iso_utc(now)
    end = parse_iso_utc(end_at) if end_at else now
    if start_at:
        start = parse_iso_utc(start_at)
    elif history_years == 0:
        # Explicit "no lookback" is still an empty-ish window; the caller
        # decides validity. Anchor at epoch for a deterministic boundary.
        start = datetime(1970, 1, 1, tzinfo=timezone.utc)
    else:
        start = _calendar_subtract_years(end, history_years)
    if start >= end:
        raise ValueError(
            f"empty time window: start_at {start.isoformat()} "
            f">= end_at {end.isoformat()}"
        )
    return start, end


def in_window(dt: datetime, start: datetime, end: datetime) -> bool:
    """Half-open ``[start, end)`` test (UTC-normalized)."""
    dt = parse_iso_utc(dt)
    return start <= dt < end


# ---------------------------------------------------------------------------
# Target parsing
# ---------------------------------------------------------------------------

def normalize_handle(value: str) -> str:
    """Normalize ``@Handle`` / bare handle to lower-case handle."""
    m = HANDLE_RE.match((value or "").strip())
    if not m:
        raise ValueError(f"invalid X handle: {value!r}")
    return m.group(1).lower()


def classify_target(target: str) -> Tuple[str, Optional[str]]:
    """Classify the ``--archive-x-user`` argument.

    Returns ``(kind, handle)`` where kind is one of
    ``handle`` / ``profile_url`` / ``status_url``; handle is None for a
    status URL (the runner resolves its author via scrape_tweet).
    """
    text = (target or "").strip()
    if not text:
        raise ValueError("empty archive target")
    m = _STATUS_URL_RE.match(text)
    if m:
        return "status_url", m.group(1).lower()
    if text.startswith("http://") or text.startswith("https://"):
        pm = _PROFILE_URL_RE.match(text)
        if not pm:
            raise ValueError(f"unrecognized X profile URL: {target!r}")
        return "profile_url", pm.group(1).lower()
    return "handle", normalize_handle(text)


# ---------------------------------------------------------------------------
# Content record
# ---------------------------------------------------------------------------

@dataclass
class ContentRecord:
    """One manifest row (spec section 7).

    ``author_id`` is the archive owner (lower-case target handle).
    ``status_author`` is the handle owning the status URL; it differs
    from author_id only for reposts (probe-verified, see module docstring).
    """

    tweet_id: str
    author_id: str
    status_author: str
    post_url: str
    published_at: datetime
    discovered_at: datetime
    content_type: str = ContentKind.UNKNOWN
    is_reply: bool = False
    is_repost: bool = False
    is_pinned: bool = False
    title: Optional[str] = None
    status: str = DownloadStatus.PENDING
    archive_path: Optional[str] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    attempts: int = 0
    skip_reason: Optional[str] = None
    # Cross-check/diagnostic only: DOM time[datetime] seen at enumeration
    dom_datetime: Optional[str] = None

    @property
    def key(self) -> Tuple[str, str]:
        return self.author_id, self.tweet_id

    def validate(self) -> None:
        if not TWEET_ID_RE.match(str(self.tweet_id)):
            raise ValueError(f"invalid tweet_id: {self.tweet_id!r}")
        if self.author_id != normalize_handle(self.author_id):
            raise ValueError("author_id must be normalized lower-case")
        if self.status_author != normalize_handle(self.status_author):
            raise ValueError("status_author must be normalized lower-case")
        # published_at must agree with the snowflake id
        snowflake_to_datetime(self.tweet_id)
        expected = (
            f"https://x.com/{self.status_author}/status/{self.tweet_id}"
        )
        if self.post_url != expected:
            raise ValueError(
                f"post_url mismatch: {self.post_url!r} != {expected!r}"
            )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tweet_id": self.tweet_id,
            "author_id": self.author_id,
            "status_author": self.status_author,
            "post_url": self.post_url,
            "published_at": self.published_at.astimezone(
                timezone.utc
            ).isoformat(),
            "discovered_at": self.discovered_at.astimezone(
                timezone.utc
            ).isoformat(),
            "content_type": self.content_type,
            "is_reply": self.is_reply,
            "is_repost": self.is_repost,
            "is_pinned": self.is_pinned,
            "title": self.title,
            "status": self.status,
            "archive_path": self.archive_path,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "attempts": self.attempts,
            "skip_reason": self.skip_reason,
            "dom_datetime": self.dom_datetime,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ContentRecord":
        return cls(
            tweet_id=str(data["tweet_id"]),
            author_id=data["author_id"],
            status_author=data.get("status_author", data["author_id"]),
            post_url=data["post_url"],
            published_at=parse_iso_utc(data["published_at"]),
            discovered_at=parse_iso_utc(
                data.get("discovered_at") or datetime.now(timezone.utc)
            ),
            content_type=data.get("content_type", ContentKind.UNKNOWN),
            is_reply=bool(data.get("is_reply", False)),
            is_repost=bool(data.get("is_repost", False)),
            is_pinned=bool(data.get("is_pinned", False)),
            title=data.get("title"),
            status=data.get("status", DownloadStatus.PENDING),
            archive_path=data.get("archive_path"),
            error_code=data.get("error_code"),
            error_message=data.get("error_message"),
            attempts=int(data.get("attempts", 0)),
            skip_reason=data.get("skip_reason"),
            dom_datetime=data.get("dom_datetime"),
        )


def build_record(
    tweet_id: str,
    target_handle: str,
    *,
    status_author: Optional[str] = None,
    dom_datetime: Optional[str] = None,
    is_reply: bool = False,
    is_repost: bool = False,
    is_pinned: bool = False,
    content_type: str = ContentKind.UNKNOWN,
    discovered_at: Optional[datetime] = None,
) -> ContentRecord:
    """Construct a validated record from an enumeration observation."""
    tweet_id = str(tweet_id).strip()
    owner = normalize_handle(target_handle)
    link_author = normalize_handle(status_author or owner)
    published = snowflake_to_datetime(tweet_id)
    record = ContentRecord(
        tweet_id=tweet_id,
        author_id=owner,
        status_author=link_author,
        post_url=f"https://x.com/{link_author}/status/{tweet_id}",
        published_at=published,
        discovered_at=(discovered_at or datetime.now(timezone.utc)),
        content_type=content_type,
        is_reply=is_reply,
        is_repost=is_repost,
        is_pinned=is_pinned,
        dom_datetime=dom_datetime,
    )
    record.validate()
    return record


def is_in_scope(link_author: str, target_handle: str, is_repost: bool) -> bool:
    """Scope rule for a raw timeline observation.

    Own tweets and reposts (probe-verified: repost permalink points at
    the original author) are in scope; other authors' conversation
    context tweets from /with_replies are out of scope.
    """
    return (
        normalize_handle(link_author) == normalize_handle(target_handle)
        or is_repost
    )
