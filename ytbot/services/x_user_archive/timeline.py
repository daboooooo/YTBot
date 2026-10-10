"""Timeline enumerator for X user archives (spec sections 3, 9).

Phase-1 real-account probe findings baked into the design
(dev/probe_timeline_enumeration.py, dev/x_archive_phase1_findings.json):

* Logged-in scrolling works with the EXISTING extractor context
  (same cookies/fingerprint); ~14 new entries per polite round.
* 344 real samples: snowflake-derived time == DOM time within 1s,
  so snowflake time drives filtering and ordering; DOM time is only a
  cross-check (mismatches are recorded, never fatal).
* Repost entries: social-context label ``"<name> 已转帖"`` /
  "reposted", permalink points at the ORIGINAL author. The Posts tab
  carries reposts inline; a dedicated ``/<handle>/reposts`` tab also
  exists (not consumed in v1, recorded as a future channel).
* ``/with_replies`` interleaves unmarked other-author context tweets;
  scope filtering relies on link author + repost marker.
* Articles tab was ABSENT for the probed account and /articles
  rendered a plain profile page: the channel is opportunistic only,
  enum_status must not claim ``complete`` coverage of long-form posts.
* Real scroll-depth limit and rate-limit pages were not exercised in
  the polite probe budget; their DOM handling below is therefore
  conservative and labelled unverified.
"""

import asyncio
import random
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Callable, Dict, List, Optional

from ...core.enhanced_logger import get_logger
from .models import (
    ARTICLE_URL_RE,
    ContentKind,
    ContentRecord,
    EnumStatus,
    EnumStopReason,
    build_record,
    in_window,
    is_in_scope,
    normalize_handle,
    snowflake_to_datetime,
)

logger = get_logger(__name__)

_STATUS_HREF_RE = re.compile(
    r"^/?([A-Za-z0-9_]{1,15})/status/(\d+)(?:/photo/\d+)?$"
)

# --- in-page JS: profile tabs -------------------------------------------
TABS_JS = r"""
() => {
    const out = [];
    document.querySelectorAll('a[role="tab"], [role="tab"] a').forEach((t) => {
        const href = t.getAttribute('href');
        if (href) out.push({text: t.textContent.trim(), href: href});
    });
    return out;
}
"""

# --- in-page JS: collect every visible timeline cell --------------------
COLLECT_JS = r"""
() => {
    const entries = [];
    const arts = document.querySelectorAll('article[data-testid="tweet"]');
    for (const art of arts) {
        let href = null;
        let domTime = null;
        const timeEl = art.querySelector('time');
        if (timeEl) {
            domTime = timeEl.getAttribute('datetime') || null;
            let node = timeEl;
            for (let i = 0; i < 6 && node; i++) {
                if (node.tagName === 'A' && node.getAttribute('href')) {
                    href = node.getAttribute('href');
                    break;
                }
                node = node.parentElement;
            }
        }
        if (!href) {
            const a = art.querySelector('a[href*="/status/"]');
            if (a) href = a.getAttribute('href');
        }
        const ctxEl = art.querySelector('[data-testid="socialContext"]');
        const socialContext = ctxEl ? ctxEl.textContent.trim() : '';
        let articleHref = null;
        const artA = art.querySelector(
            'a[href*="/article/"], a[href*="/i/article/"]'
        );
        if (artA) articleHref = artA.getAttribute('href');
        // "Replying to @x" lives in tweet body, not in socialContext
        let replyingTo = '';
        const text = art.innerText || '';
        const m = text.match(/(?:Replying to|回复)\s+@?([A-Za-z0-9_]{1,15})/);
        if (m) replyingTo = m[1];
        entries.push({
            href: href,
            dom_time: domTime,
            social_context: socialContext,
            article_href: articleHref,
            replying_to: replyingTo
        });
    }
    const primary = document.querySelector('[data-testid="primaryColumn"]');
    const head = (primary ? primary.innerText : document.body.innerText)
        .slice(0, 1200);
    return {
        entries: entries,
        scroll_y: window.scrollY,
        doc_height: document.body.scrollHeight,
        article_count: document.querySelectorAll('article').length,
        head: head,
        has_login_button: !!document.querySelector(
            '[data-testid="loginButton"], a[href="/login"]'
        )
    };
}
"""

SCROLL_JS = "window.scrollTo(0, document.body.scrollHeight);"

# Full-page systemic signals (conservative; unverified against live pages)
_RATE_TOKENS = (
    "rate limit", "rate limited", "too many requests",
    "速率限制", "请求过多",
)


@dataclass
class EnumerateOptions:
    target_handle: str
    start_at: datetime
    end_at: datetime
    include_replies: bool = False
    include_reposts: bool = True
    max_posts: int = 1000           # 0 == unlimited
    scroll_stall: int = 8
    request_delay: float = 2.0
    page_error_limit: int = 3
    max_rounds: Optional[int] = None  # hard safety cap, None = unlimited


@dataclass
class EnumerationOutcome:
    stop_reason: str = EnumStopReason.UNKNOWN
    enum_status: str = EnumStatus.UNKNOWN
    tabs: List[Dict[str, str]] = field(default_factory=list)
    rounds: List[Dict[str, Any]] = field(default_factory=list)
    raw_discovered: int = 0
    in_scope_count: int = 0
    out_of_scope: int = 0
    out_of_range: int = 0
    invalid: int = 0
    reposts_excluded: int = 0
    articles_seen: int = 0
    articles_channel: str = "unknown"  # present/absent/unknown
    snowflake_mismatches: List[Dict[str, Any]] = field(default_factory=list)
    article_only_entries: int = 0
    detail: str = ""


class TimelineEnumerationError(Exception):
    """Systemic enumeration failure (login wall / rate limit / errors)."""

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason
        self.message = message


def _has_any(text: str, tokens) -> bool:
    low = text.lower()
    return any(t in low for t in tokens)


class TimelineEnumerator:
    """Scroll a user timeline and yield in-scope, in-window record batches.

    The browser/context is always the extractor's own (never a separate
    Playwright stack). Caller appends yielded batches to the manifest;
    the final dedupe/sort/rewrite happens after enumeration ends.
    """

    def __init__(
        self,
        extractor: Any,
        options: EnumerateOptions,
        sleep_func: Optional[Callable[[float], Any]] = None,
    ):
        self.extractor = extractor
        self.options = options
        self._sleep = sleep_func or (lambda d: asyncio.sleep(d))
        self.outcome = EnumerationOutcome()
        self._seen_ids = set()       # raw status ids observed this run
        self._accepted_ids = set()   # in-scope/in-window unique ids
        self._page = None
        self._owned_page = False

    async def enumerate(self) -> AsyncIterator[List[ContentRecord]]:
        opts = self.options
        handle = normalize_handle(opts.target_handle)
        await self.extractor.initialize_browser()
        self._page = await self.extractor.context.new_page()
        self._owned_page = True
        await self._page.set_extra_http_headers(
            {"Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"}
        )

        tab_urls = [f"https://x.com/{handle}"]
        if opts.include_replies:
            tab_urls.append(f"https://x.com/{handle}/with_replies")

        try:
            for tab_index, url in enumerate(tab_urls):
                async for batch in self._enumerate_tab(
                    url, handle, first_tab=(tab_index == 0)
                ):
                    yield batch
                if self.outcome.stop_reason in (
                    EnumStopReason.START_BOUNDARY,
                    EnumStopReason.MAX_POSTS,
                ):
                    break
            self._finalize_status()
        except (KeyboardInterrupt, asyncio.CancelledError):
            # Batches already yielded are persisted by the caller.
            self.outcome.stop_reason = EnumStopReason.INTERRUPTED
            self.outcome.enum_status = EnumStatus.PARTIAL
            raise
        finally:
            if self._owned_page and self._page is not None:
                try:
                    await self._page.close()
                except Exception:  # noqa: BLE001
                    pass

    async def _enumerate_tab(
        self, url: str, handle: str, first_tab: bool
    ) -> AsyncIterator[List[ContentRecord]]:
        opts = self.options
        try:
            response = await self._page.goto(
                url, wait_until="domcontentloaded", timeout=90000
            )
        except Exception as exc:  # noqa: BLE001
            raise TimelineEnumerationError(
                EnumStopReason.LOGIN_WALL,
                f"profile navigation failed: {type(exc).__name__}: {exc}",
            )

        status = response.status if response else None
        if status in (401, 403, 429):
            raise TimelineEnumerationError(
                EnumStopReason.LOGIN_WALL if status != 429
                else EnumStopReason.RATE_LIMITED,
                f"profile returned HTTP {status}",
            )

        try:
            await self._page.wait_for_selector(
                "article[data-testid='tweet']", timeout=20000
            )
        except Exception:  # noqa: BLE001
            pass
        await self._pause(1.5)

        if first_tab:
            self.outcome.tabs = await self._read_tabs()
            self.outcome.articles_channel = (
                "present" if any(
                    (t.get("href") or "").rstrip("/").endswith("/articles")
                    for t in self.outcome.tabs
                )
                else "absent"
            )

        stall = 0
        page_errors = 0
        boundary_confirm_rounds = 0
        round_idx = 0

        while True:
            round_idx += 1
            try:
                raw = await self._page.evaluate(COLLECT_JS)
                page_errors = 0
            except Exception as exc:  # noqa: BLE001
                page_errors += 1
                logger.warning("timeline evaluate error %s: %s",
                               page_errors, exc)
                self.outcome.rounds.append(
                    {"round": round_idx, "error": str(exc)}
                )
                if page_errors >= opts.page_error_limit:
                    raise TimelineEnumerationError(
                        EnumStopReason.PAGE_ERROR_LIMIT,
                        f"{page_errors} consecutive page errors",
                    )
                await self._pause(opts.request_delay)
                continue

            records, new_ids, saw_old = self._parse_raw(raw, handle)
            self._check_systemic(raw, saw_old)

            self.outcome.rounds.append({
                "round": round_idx,
                "url": url,
                "new_ids": new_ids,
                "accepted_total": len(self._accepted_ids),
                "articles_in_dom": raw.get("article_count"),
                "scroll_y": raw.get("scroll_y"),
            })
            logger.info(
                "enum %s round %s: new=%s accepted=%s",
                url, round_idx, new_ids, len(self._accepted_ids),
            )

            if records:
                yield records

            # --- stop conditions --------------------------------------
            if self._accepted_ids and self._hit_max_posts():
                self.outcome.stop_reason = EnumStopReason.MAX_POSTS
                self.outcome.detail = (
                    f"--max-posts {opts.max_posts} reached"
                )
                return

            if saw_old:
                boundary_confirm_rounds += 1
                # spec 8.1.5: keep one more page to confirm no stragglers
                if boundary_confirm_rounds >= 2:
                    self.outcome.stop_reason = EnumStopReason.START_BOUNDARY
                    return
            else:
                boundary_confirm_rounds = 0

            if new_ids == 0:
                stall += 1
                if stall >= opts.scroll_stall:
                    self.outcome.stop_reason = EnumStopReason.STALL
                    return
            else:
                stall = 0

            if opts.max_rounds is not None and round_idx >= opts.max_rounds:
                # Safety cap (not a user-facing knob); treat as unknown.
                self.outcome.stop_reason = EnumStopReason.UNKNOWN
                self.outcome.detail = "internal round safety cap reached"
                return

            await self._page.evaluate(SCROLL_JS)
            await self._pause(opts.request_delay)

    async def _read_tabs(self) -> List[Dict[str, str]]:
        try:
            return await self._page.evaluate(TABS_JS)
        except Exception:  # noqa: BLE001
            return []

    def _check_systemic(self, raw: Dict[str, Any], saw_old: bool) -> None:
        # An old-entry boundary is never a systemic page; trust it first.
        if saw_old:
            return
        head = raw.get("head", "") or ""
        if raw.get("has_login_button") and raw.get("article_count", 0) == 0:
            raise TimelineEnumerationError(
                EnumStopReason.LOGIN_WALL,
                "login button present with no timeline content",
            )
        if _has_any(head, _RATE_TOKENS):
            raise TimelineEnumerationError(
                EnumStopReason.RATE_LIMITED,
                "rate-limit message detected on timeline",
            )

    def _parse_raw(self, raw: Dict[str, Any], handle: str):
        """Translate one DOM snapshot.

        Returns ``(accepted_records, new_id_count, saw_old)``. Age is
        evaluated for EVERY visible entry (including duplicates), so the
        post-boundary confirmation round works; other counters count
        unique observations only.
        """
        batch_records: List[ContentRecord] = []
        new_ids = 0
        saw_old = False
        now = datetime.now(timezone.utc)
        opts = self.options

        for entry in raw.get("entries", []):
            href = entry.get("href")
            if not href:
                # Article-only cell without a status link: counted, not
                # downloaded (no stable snowflake id; channel unverified).
                if entry.get("article_href"):
                    self.outcome.article_only_entries += 1
                continue
            m = _STATUS_HREF_RE.match(href)
            if not m:
                continue
            link_author, tweet_id = m.group(1).lower(), m.group(2)

            social = entry.get("social_context", "") or ""
            is_pinned = ("pinned" in social.lower()) or ("置顶" in social)
            is_repost = (
                ("repost" in social.lower())
                or ("转帖" in social)
                or ("转推" in social)
            )
            is_reply = bool(entry.get("replying_to"))
            article_href = entry.get("article_href")

            try:
                published = snowflake_to_datetime(tweet_id)
            except ValueError:
                if tweet_id not in self._seen_ids:
                    self.outcome.invalid += 1
                    self._seen_ids.add(tweet_id)
                    new_ids += 1
                continue
            # Boundary signal uses every visible entry, even duplicates.
            if published < opts.start_at:
                saw_old = True

            if tweet_id in self._seen_ids:
                continue
            self._seen_ids.add(tweet_id)
            new_ids += 1
            self.outcome.raw_discovered += 1

            if article_href and ARTICLE_URL_RE.search(article_href):
                self.outcome.articles_seen += 1
            if entry.get("dom_time"):
                self._cross_check(tweet_id, entry["dom_time"], published)

            if not in_window(published, opts.start_at, opts.end_at):
                self.outcome.out_of_range += 1
                continue
            if not is_in_scope(link_author, handle, is_repost):
                self.outcome.out_of_scope += 1
                continue
            if is_repost and not opts.include_reposts:
                self.outcome.reposts_excluded += 1
                continue

            if tweet_id in self._accepted_ids:
                continue
            if self._hit_max_posts():
                # Over the cap within this batch: observed but not accepted
                continue
            self._accepted_ids.add(tweet_id)
            self.outcome.in_scope_count += 1

            batch_records.append(build_record(
                tweet_id, handle,
                status_author=link_author,
                dom_datetime=entry.get("dom_time"),
                is_reply=is_reply,
                is_repost=is_repost,
                is_pinned=is_pinned,
                content_type=(
                    ContentKind.ARTICLE if article_href else ContentKind.POST
                ),
                discovered_at=now,
            ))

        return batch_records, new_ids, saw_old

    def _cross_check(
        self, tweet_id: str, dom_time: str, snowflake_dt: datetime
    ) -> None:
        try:
            dom = dom_time.replace("Z", "+00:00")
            dom_dt = datetime.fromisoformat(dom)
            if dom_dt.tzinfo is None:
                dom_dt = dom_dt.replace(tzinfo=timezone.utc)
            diff = abs(
                (snowflake_dt - dom_dt.astimezone(timezone.utc)).total_seconds()
            )
            if diff > 60:
                self.outcome.snowflake_mismatches.append({
                    "tweet_id": tweet_id,
                    "dom_time": dom_time,
                    "diff_seconds": diff,
                })
        except (ValueError, TypeError):
            pass

    def _hit_max_posts(self) -> bool:
        limit = self.options.max_posts
        return limit > 0 and len(self._accepted_ids) >= limit

    def _finalize_status(self) -> None:
        stop = self.outcome.stop_reason
        if stop == EnumStopReason.START_BOUNDARY:
            # Spec 3.4: complete also requires no anomalies during scroll
            base = EnumStatus.COMPLETE
            if self.outcome.snowflake_mismatches:
                base = EnumStatus.PARTIAL
            if self.outcome.articles_channel != "present":
                # Long-form channel unavailable/unproven (spec 3.3.6)
                base = EnumStatus.PARTIAL
            self.outcome.enum_status = base
        elif stop == EnumStopReason.MAX_POSTS:
            self.outcome.enum_status = EnumStatus.PARTIAL
        elif stop == EnumStopReason.STALL:
            self.outcome.enum_status = EnumStatus.PARTIAL
            self.outcome.detail = (
                self.outcome.detail
                or "scroll stall before reaching start_at (history depth "
                   "limit or rate-limited truncation)"
            )
        else:
            self.outcome.enum_status = EnumStatus.UNKNOWN

    async def _pause(self, seconds: float) -> None:
        if seconds <= 0:
            return
        jitter = random.uniform(0, min(0.5, seconds * 0.25))
        await self._sleep(seconds + jitter)
