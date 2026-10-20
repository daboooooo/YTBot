"""Per-post archiving: reuse TwitterHandler.download_content, place by
PUBLISH time into the archive root (spec sections 11, 12).

The existing downloader's own storage rules (download-time month,
Nextcloud queue) are untouched: this module only moves the handler's
temp-directory product into ``<content_root>/<YYYY>/<MM>/<dir>/`` and
updates the manifest record. No file inside the product is regenerated.
"""

import asyncio
import os
import random
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from ...core.enhanced_logger import get_logger
from ...core.types import ContentType
from ...utils.common import sanitize_filename
from .models import (
    ContentKind,
    ContentRecord,
    DownloadStatus,
    SkipReason,
)

logger = get_logger(__name__)

# Error codes produced by TwitterHandler._parse_twitter_error
SYSTEMIC_CODES = {"ERROR_LOGIN_FAILED", "ERROR_BROWSER_CLOSED"}
PERMANENT_CODES = {
    "ERROR_TWEET_NOT_FOUND",
    "ERROR_ACCOUNT_SUSPENDED",
    "ERROR_TWEET_PROTECTED",
}
RETRYABLE_CODES = {"ERROR_RATE_LIMITED", "ERROR_TWITTER_UNKNOWN"}
_NETWORK_HINTS = ("timeout", "timed out", "connection", "temporarily")
# Playwright's TargetClosedError text: the browser process itself died,
# so blind per-post backoff retries cannot help.
_BROWSER_CLOSED_HINT = "target page, context or browser has been closed"


class ArchivePause(Exception):
    """Systemic failure: pause the whole task for --resume."""

    def __init__(self, reason: str, code: str, message: str):
        super().__init__(message)
        self.reason = reason
        self.code = code
        self.message = message


@dataclass
class ArchiveOptions:
    request_delay: float = 2.0
    max_attempts: int = 3
    backoff_base: float = 5.0
    backoff_cap: float = 60.0
    title_byte_budget: int = 80


def post_dir_name(record: ContentRecord, title: Optional[str],
                  budget: int = ArchiveOptions.title_byte_budget) -> str:
    """``YYYY-MM-DD_<title>_<short_id>`` — stable, never random suffix."""
    date = record.published_at.strftime("%Y-%m-%d")
    short_id = record.tweet_id[-6:]
    if title and title.strip():
        slug = sanitize_filename(title.strip(), max_length=budget)
    else:
        kind = (
            "article" if record.content_type == ContentKind.ARTICLE
            else "post"
        )
        slug = f"{kind}_{record.tweet_id}"
    return f"{date}_{slug}_{short_id}"


def is_valid_content_dir(path: Path) -> bool:
    """A completed product: directory containing a non-empty .html."""
    if not path.is_dir():
        return False
    for html in path.glob("*.html"):
        try:
            if html.is_file() and html.stat().st_size > 0:
                return True
        except OSError:
            continue
    return False


def find_single_pdf(path: Path) -> Optional[Path]:
    if not path.is_dir():
        return None
    pdfs = sorted(p for p in path.glob("*.pdf") if p.is_file())
    return pdfs[0] if pdfs else None


class PostArchiver:
    def __init__(
        self,
        handler: Any,
        content_root,
        options: Optional[ArchiveOptions] = None,
        sleep_func: Optional[Callable[[float], Any]] = None,
    ):
        self.handler = handler
        self.content_root = Path(content_root)
        self.content_root.mkdir(parents=True, exist_ok=True)
        self.options = options or ArchiveOptions()
        self._sleep = sleep_func or (lambda d: asyncio.sleep(d))

    # -- public API -------------------------------------------------------

    async def archive(self, record: ContentRecord) -> str:
        """Archive one record. Returns the resulting DownloadStatus.

        DOWNLOADED records with a valid product are skipped without any
        network access. Mutates the record in place.
        """
        target = self._target_path(record, record.title)
        if record.status == DownloadStatus.DOWNLOADED:
            if is_valid_content_dir(target):
                record.archive_path = str(target)
                record.skip_reason = SkipReason.ALREADY_ARCHIVED
                return DownloadStatus.SKIPPED
            # product vanished/partial: fall through and re-download

        replacing = record.status == DownloadStatus.DOWNLOADING
        last_code = "ERROR_TWITTER_UNKNOWN"
        last_message = ""

        for attempt in range(1, self.options.max_attempts + 1):
            record.attempts = attempt
            record.status = DownloadStatus.DOWNLOADING
            result = await self._download_one(record.post_url)

            if result["ok"]:
                placed = self._apply_success(
                    record, result, replacing=replacing
                )
                if placed == "downloaded":
                    return DownloadStatus.DOWNLOADED
                if placed == "skipped":
                    return DownloadStatus.SKIPPED
                # product itself was unusable: counted as a failed attempt
                last_code = record.error_code or "ERROR_TWITTER_UNKNOWN"
                last_message = record.error_message or "bad product"
            else:
                last_code = result["code"]
                last_message = result["message"]
                record.error_code = last_code
                record.error_message = last_message

            if last_code in SYSTEMIC_CODES:
                record.status = DownloadStatus.RETRYABLE_ERROR
                reason = (
                    "browser_closed"
                    if last_code == "ERROR_BROWSER_CLOSED"
                    else "login_failed"
                )
                raise ArchivePause(
                    reason, last_code, last_message
                )
            if last_code in PERMANENT_CODES:
                record.status = DownloadStatus.PERMANENT_ERROR
                return DownloadStatus.PERMANENT_ERROR
            if attempt >= self.options.max_attempts:
                break
            await self._backoff(attempt)

        record.status = DownloadStatus.RETRYABLE_ERROR
        return DownloadStatus.RETRYABLE_ERROR

    async def polite_delay(self) -> None:
        if self.options.request_delay > 0:
            jitter = random.uniform(
                0, min(0.5, self.options.request_delay * 0.25)
            )
            await self._sleep(self.options.request_delay + jitter)

    # -- internals --------------------------------------------------------

    def _target_path(
        self, record: ContentRecord, title: Optional[str]
    ) -> Path:
        year = record.published_at.strftime("%Y")
        month = record.published_at.strftime("%m")
        name = post_dir_name(record, title, self.options.title_byte_budget)
        target = (self.content_root / year / month / name)
        self._assert_within_root(target)
        return target

    def _assert_within_root(self, target: Path) -> None:
        root = os.path.abspath(self.content_root)
        wanted = os.path.abspath(target)
        if os.path.commonpath([root, wanted]) != root:
            raise ValueError(
                f"refusing path outside archive root: {target}"
            )

    async def _download_one(self, url: str) -> dict:
        try:
            result = await self.handler.download_content(
                url, ContentType.TEXT, archive_mode=True
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("download raised for %s: %s", url, exc)
            return {
                "ok": False,
                "code": self._classify_exception(exc),
                "message": f"{type(exc).__name__}: {exc}",
                "result": None,
            }

        if getattr(result, "success", False):
            return {"ok": True, "code": None, "message": None,
                    "result": result}

        message = getattr(result, "error_message", None) or "download failed"
        code = getattr(result, "error_detail", None) or \
            self._parse_code(message)
        return {"ok": False, "code": code, "message": message,
                "result": result}

    def _parse_code(self, message: str) -> str:
        parser = getattr(self.handler, "_parse_twitter_error", None)
        if parser is not None:
            try:
                return parser(message)
            except Exception:  # noqa: BLE001
                pass
        low = (message or "").lower()
        if _BROWSER_CLOSED_HINT in low:
            return "ERROR_BROWSER_CLOSED"
        if "登录" in message or "login" in low:
            return "ERROR_LOGIN_FAILED"
        if "rate limit" in low or "速率" in message:
            return "ERROR_RATE_LIMITED"
        return "ERROR_TWITTER_UNKNOWN"

    def _classify_exception(self, exc: Exception) -> str:
        if _BROWSER_CLOSED_HINT in str(exc).lower():
            return "ERROR_BROWSER_CLOSED"
        if isinstance(exc, asyncio.TimeoutError):
            return "ERROR_RATE_LIMITED"
        low = str(exc).lower()
        if any(hint in low for hint in _NETWORK_HINTS):
            return "ERROR_RATE_LIMITED"
        return "ERROR_TWITTER_UNKNOWN"

    def _apply_success(
        self, record: ContentRecord, result_data: dict,
        replacing: bool,
    ) -> str:
        """Outcome: "downloaded", "skipped" (valid dir already exists),
        or "failed" (unusable product, counted as one attempt)."""
        result = result_data["result"]
        temp_dir = getattr(result, "temp_dir", None) or \
            getattr(result, "file_path", None)
        if not temp_dir or not Path(temp_dir).is_dir() or \
                not any(Path(temp_dir).iterdir()):
            record.error_code = "ERROR_EMPTY_PRODUCT"
            record.error_message = "empty download product"
            record.status = DownloadStatus.RETRYABLE_ERROR
            if temp_dir:
                shutil.rmtree(temp_dir, ignore_errors=True)
            return "failed"
        if not is_valid_content_dir(Path(temp_dir)):
            # cleanup and treat as a failed attempt
            shutil.rmtree(temp_dir, ignore_errors=True)
            record.error_code = "ERROR_NO_HTML"
            record.error_message = "downloaded product has no html"
            record.status = DownloadStatus.RETRYABLE_ERROR
            return "failed"

        info = getattr(result, "content_info", None)
        title = getattr(info, "title", None) if info else None
        metadata = getattr(info, "metadata", None) if info else None
        if isinstance(metadata, dict) and \
                metadata.get("post_type") == "article":
            record.content_type = ContentKind.ARTICLE
        target = self._target_path(record, title)

        # Another valid product may exist even if manifest state lagged
        if not replacing and target.exists():
            if is_valid_content_dir(target):
                shutil.rmtree(temp_dir, ignore_errors=True)
                record.title = title
                record.archive_path = str(target)
                record.error_code = None
                record.error_message = None
                record.skip_reason = SkipReason.ALREADY_ARCHIVED
                record.status = DownloadStatus.SKIPPED
                return "skipped"
            shutil.rmtree(target, ignore_errors=True)

        self._place(temp_dir, target, replacing=replacing)

        record.title = title
        record.archive_path = str(target)
        record.error_code = None
        record.error_message = None
        record.status = DownloadStatus.DOWNLOADED
        logger.info("archived %s -> %s", record.tweet_id, target)
        return "downloaded"

    def _place(self, temp_dir: str, target: Path, replacing: bool) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        if replacing and target.exists():
            # Nearly-atomic swap: move residue aside, move product in,
            # then remove residue. shutil.move covers cross-device /tmp.
            obsolete = target.with_name(
                f"{target.name}.obsolete.{os.getpid()}"
            )
            if obsolete.exists():
                shutil.rmtree(obsolete, ignore_errors=True)
            os.rename(target, obsolete)
            try:
                shutil.move(temp_dir, str(target))
            except Exception:
                # roll the residue back on failure
                if not target.exists():
                    os.rename(obsolete, target)
                raise
            shutil.rmtree(obsolete, ignore_errors=True)
        else:
            shutil.move(temp_dir, str(target))

    async def _backoff(self, attempt: int) -> None:
        delay = min(
            self.options.backoff_cap,
            self.options.backoff_base * (2 ** (attempt - 1)),
        )
        delay += random.uniform(0, min(1.0, delay * 0.1))
        logger.info("retrying after %.1fs (attempt %s)", delay, attempt + 1)
        await self._sleep(delay)
