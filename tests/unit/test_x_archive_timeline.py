"""Fake-page driven tests for TimelineEnumerator (no browser, no network)."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from ytbot.services.x_user_archive.models import (
    TWITTER_SNOWFLAKE_EPOCH_MS,
    EnumStatus,
    EnumStopReason,
)
from ytbot.services.x_user_archive.timeline import (
    EnumerateOptions,
    TimelineEnumerationError,
    TimelineEnumerator,
)

UTC = timezone.utc
START = datetime(2025, 1, 1, tzinfo=UTC)
END = datetime(2025, 4, 1, tzinfo=UTC)


def tweet_id_at(dt: datetime, seq: int = 0) -> str:
    ms = int(dt.timestamp() * 1000)
    return str(((ms - TWITTER_SNOWFLAKE_EPOCH_MS) << 22) | seq)


def dom_entry(handle, dt, seq=0, social="", article=None,
              reply_to="", href=None):
    tweet_id = tweet_id_at(dt, seq)
    return {
        "href": href or f"/{handle}/status/{tweet_id}",
        "dom_time": dt.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "social_context": social,
        "article_href": article,
        "replying_to": reply_to,
    }


def snapshot(entries, *, head="", login_button=False):
    return {
        "entries": entries,
        "scroll_y": 1000,
        "doc_height": 5000,
        "article_count": len(entries),
        "head": head,
        "has_login_button": login_button,
    }


EMPTY = snapshot([])


class FakeResponse:
    def __init__(self, status=200):
        self.status = status


class FakePage:
    """Scripts collector snapshots per URL; counts scroll calls."""

    def __init__(self, scripts, *, status=200, eval_error=None,
                 tabs=None, sleep_raises=None):
        self.scripts = scripts          # url-substring -> [snapshots]
        self.status = status
        self.eval_error = eval_error
        self.tabs = tabs if tabs is not None else [
            {"text": "帖子", "href": "/elonmusk"},
            {"text": "回复", "href": "/elonmusk/with_replies"},
            {"text": "转帖", "href": "/elonmusk/reposts"},
            {"text": "媒体", "href": "/elonmusk/media"},
        ]
        self.sleep_raises = sleep_raises
        self.visited_urls = []
        self.scroll_calls = 0
        self.collect_calls = 0
        self._idx = {}

    async def set_extra_http_headers(self, headers):
        pass

    async def wait_for_timeout(self, ms):
        if self.sleep_raises:
            raise self.sleep_raises

    async def wait_for_selector(self, selector, timeout=None):
        return True

    async def goto(self, url, wait_until=None, timeout=None):
        self.visited_urls.append(url)
        if self.status >= 400:
            pass
        return FakeResponse(self.status)

    async def close(self):
        pass

    async def evaluate(self, js):
        if self.eval_error is not None:
            raise self.eval_error
        if 'role="tab"' in js:
            return self.tabs
        if "scrollTo" in js:
            self.scroll_calls += 1
            return None
        # collector: prefer exact path-suffix matches (e.g. /with_replies)
        # over substring matches (e.g. /elonmusk)
        self.collect_calls += 1
        url = self.visited_urls[-1].rstrip("/")
        suffix = [k for k in self.scripts if url.endswith(k.rstrip("/"))]
        partial = [k for k in self.scripts if k in self.visited_urls[-1]]
        key = (suffix[0] if suffix else (partial[0] if partial else None))
        idx = self._idx.setdefault(key, 0)
        bucket = self.scripts.get(key, [])
        snap = bucket[idx] if idx < len(bucket) else EMPTY
        self._idx[key] = idx + 1
        return snap


class FakeExtractor:
    def __init__(self, page):
        self._page = page
        self.initialized = False

    async def initialize_browser(self):
        self.initialized = True

    @property
    def context(self):
        outer = self

        class Ctx:
            async def new_page(self_inner):
                return outer._page

        return Ctx()


def options(**kw):
    base = dict(
        target_handle="elonmusk",
        start_at=START,
        end_at=END,
        scroll_stall=2,
        request_delay=0,
        page_error_limit=3,
    )
    base.update(kw)
    return EnumerateOptions(**base)


async def collect_all(extractor, opts):
    enumerator = TimelineEnumerator(
        extractor, opts, sleep_func=lambda d: asyncio.sleep(0)
    )
    batches = []
    async for batch in enumerator.enumerate():
        batches.extend(batch)
    return enumerator, batches


# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestEnumeration:
    async def test_stall_stops_partial(self):
        e1 = dom_entry("elonmusk", datetime(2025, 2, 1, tzinfo=UTC), 1)
        e2 = dom_entry("elonmusk", datetime(2025, 2, 2, tzinfo=UTC), 2)
        page = FakePage({"x.com/elonmusk": [snapshot([e1, e2])]})
        enum, recs = await collect_all(FakeExtractor(page), options())
        assert enum.outcome.stop_reason == EnumStopReason.STALL
        assert enum.outcome.enum_status == EnumStatus.PARTIAL
        assert len(recs) == 2
        # scroll happened trying to load more, then stall after N rounds
        assert page.scroll_calls >= 1

    async def test_start_boundary_complete_when_articles_present(self):
        in_win = dom_entry("elonmusk", datetime(2025, 2, 1, tzinfo=UTC))
        old = dom_entry("elonmusk", datetime(2024, 1, 1, tzinfo=UTC))
        # Old cells stay rendered in a real browser: repeat the same
        # snapshot so the boundary confirmation round can see them.
        page = FakePage(
            {"x.com/elonmusk": [
                snapshot([in_win, old]), snapshot([in_win, old]),
            ]},
            tabs=[{"text": "帖子", "href": "/elonmusk"},
                  {"text": "Articles", "href": "/elonmusk/articles"}],
        )
        enum, recs = await collect_all(FakeExtractor(page), options())
        assert enum.outcome.stop_reason == EnumStopReason.START_BOUNDARY
        assert enum.outcome.enum_status == EnumStatus.COMPLETE
        assert len(recs) == 1  # old entry excluded

    async def test_start_boundary_partial_without_articles_channel(self):
        in_win = dom_entry("elonmusk", datetime(2025, 2, 1, tzinfo=UTC))
        old = dom_entry("elonmusk", datetime(2024, 1, 1, tzinfo=UTC))
        page = FakePage({"x.com/elonmusk": [
            snapshot([in_win, old]), snapshot([in_win, old]),
        ]})
        enum, _ = await collect_all(FakeExtractor(page), options())
        assert enum.outcome.stop_reason == EnumStopReason.START_BOUNDARY
        assert enum.outcome.enum_status == EnumStatus.PARTIAL

    async def test_max_posts_stops_without_extra_scroll(self):
        entries = [
            dom_entry("elonmusk", datetime(2025, 2, 1, tzinfo=UTC), i)
            for i in range(5)
        ]
        page = FakePage({"x.com/elonmusk": [snapshot(entries)]})
        enum, recs = await collect_all(
            FakeExtractor(page), options(max_posts=3)
        )
        assert enum.outcome.stop_reason == EnumStopReason.MAX_POSTS
        assert enum.outcome.enum_status == EnumStatus.PARTIAL
        assert len(recs) == 3
        assert page.scroll_calls == 0
        assert "max-posts 3" in enum.outcome.detail

    async def test_max_posts_zero_unlimited(self):
        entries = [
            dom_entry("elonmusk", datetime(2025, 2, 1, tzinfo=UTC), i)
            for i in range(50)
        ]
        page = FakePage({"x.com/elonmusk": [snapshot(entries)]})
        enum, recs = await collect_all(
            FakeExtractor(page), options(max_posts=0)
        )
        assert len(recs) == 50
        assert enum.outcome.stop_reason == EnumStopReason.STALL

    async def test_1005_entries_caps_at_1000(self):
        # plan 17.13: 1005 raw entries -> exactly 1000 accepted, partial
        entries = [
            dom_entry(
                "elonmusk",
                datetime(2025, 1, 2, tzinfo=UTC) + timedelta(minutes=i),
                i,
            )
            for i in range(1005)
        ]
        page = FakePage({"x.com/elonmusk": [snapshot(entries)]})
        enum, recs = await collect_all(FakeExtractor(page), options())
        assert enum.outcome.stop_reason == EnumStopReason.MAX_POSTS
        assert len(recs) == 1000
        assert page.scroll_calls == 0

    async def test_scope_filters_context_tweet_keeps_repost(self):
        own = dom_entry("elonmusk", datetime(2025, 2, 1, tzinfo=UTC), 1)
        context = dom_entry(
            "teslayoda", datetime(2025, 2, 1, tzinfo=UTC), 2
        )
        repost = dom_entry(
            "spacex", datetime(2025, 2, 2, tzinfo=UTC), 3,
            social="Elon Musk 已转帖",
        )
        page = FakePage(
            {"x.com/elonmusk": [snapshot([own, context, repost])]}
        )
        enum, recs = await collect_all(FakeExtractor(page), options())
        assert enum.outcome.out_of_scope == 1
        assert len(recs) == 2
        by_id = {r.tweet_id: r for r in recs}
        assert by_id[repost["href"].split("/")[-1]].is_repost
        assert by_id[repost["href"].split("/")[-1]].status_author == "spacex"

    async def test_no_reposts_excludes_them(self):
        repost = dom_entry(
            "spacex", datetime(2025, 2, 2, tzinfo=UTC), 3,
            social="Elonmusk reposted",
        )
        page = FakePage({"x.com/elonmusk": [snapshot([repost])]})
        enum, recs = await collect_all(
            FakeExtractor(page), options(include_reposts=False)
        )
        assert recs == []
        assert enum.outcome.reposts_excluded == 1

    async def test_out_of_range_future_entry(self):
        future = dom_entry("elonmusk", datetime(2025, 6, 1, tzinfo=UTC))
        page = FakePage({"x.com/elonmusk": [snapshot([future])]})
        enum, recs = await collect_all(FakeExtractor(page), options())
        assert recs == []
        assert enum.outcome.out_of_range == 1

    async def test_include_replies_visits_second_tab_and_dedupes(self):
        own = dom_entry("elonmusk", datetime(2025, 2, 1, tzinfo=UTC), 1)
        reply = dom_entry(
            "elonmusk", datetime(2025, 2, 3, tzinfo=UTC), 2,
            reply_to="nasa",
        )
        page = FakePage({
            "x.com/elonmusk": [snapshot([own])],
            "with_replies": [snapshot([own, reply])],
        })
        enum, recs = await collect_all(
            FakeExtractor(page), options(include_replies=True)
        )
        assert len(recs) == 2
        assert any("/with_replies" in u for u in page.visited_urls)
        assert enum.outcome.raw_discovered == 2  # duplicate not recounted

    async def test_login_wall_raises_systemic(self):
        page = FakePage(
            {"x.com/elonmusk": [snapshot([], login_button=True,
                                         head="登录")]}
        )
        with pytest.raises(TimelineEnumerationError) as ei:
            await collect_all(FakeExtractor(page), options())
        assert ei.value.reason == EnumStopReason.LOGIN_WALL

    async def test_rate_limit_raises_systemic(self):
        page = FakePage(
            {"x.com/elonmusk": [snapshot(
                [dom_entry("elonmusk", datetime(2025, 2, 1, tzinfo=UTC))],
                head="Something went wrong. Rate limit exceeded.",
            )]}
        )
        with pytest.raises(TimelineEnumerationError) as ei:
            await collect_all(FakeExtractor(page), options())
        assert ei.value.reason == EnumStopReason.RATE_LIMITED

    async def test_consecutive_page_errors_raise(self):
        page = FakePage(
            {"x.com/elonmusk": []},
            eval_error=RuntimeError("context destroyed"),
        )
        with pytest.raises(TimelineEnumerationError) as ei:
            await collect_all(FakeExtractor(page), options())
        assert ei.value.reason == EnumStopReason.PAGE_ERROR_LIMIT

    async def test_http_403_is_login_wall(self):
        page = FakePage({"x.com/elonmusk": []}, status=403)
        with pytest.raises(TimelineEnumerationError) as ei:
            await collect_all(FakeExtractor(page), options())
        assert ei.value.reason == EnumStopReason.LOGIN_WALL

    async def test_http_429_is_rate_limited(self):
        page = FakePage({"x.com/elonmusk": []}, status=429)
        with pytest.raises(TimelineEnumerationError) as ei:
            await collect_all(FakeExtractor(page), options())
        assert ei.value.reason == EnumStopReason.RATE_LIMITED

    async def test_article_marker_upgrades_content_type(self):
        art = dom_entry(
            "elonmusk", datetime(2025, 2, 1, tzinfo=UTC),
            article="/elonmusk/article/12345",
        )
        page = FakePage({"x.com/elonmusk": [snapshot([art])]})
        enum, recs = await collect_all(FakeExtractor(page), options())
        assert recs[0].content_type == "article"
        assert enum.outcome.articles_seen == 1

    async def test_interrupted_between_rounds_marked(self):
        e1 = dom_entry("elonmusk", datetime(2025, 2, 1, tzinfo=UTC), 1)
        e2 = dom_entry("elonmusk", datetime(2025, 2, 2, tzinfo=UTC), 2)
        page = FakePage(
            {"x.com/elonmusk": [snapshot([e1]), snapshot([e2])]},
        )
        extractor = FakeExtractor(page)

        pause_calls = {"n": 0}

        async def boom(_seconds):
            pause_calls["n"] += 1
            if pause_calls["n"] >= 2:  # let the pre-round pause pass
                raise asyncio.CancelledError()

        enumerator = TimelineEnumerator(
            extractor, options(scroll_stall=5, request_delay=0.01),
            sleep_func=boom,
        )
        got = []
        with pytest.raises(asyncio.CancelledError):
            async for batch in enumerator.enumerate():
                got.extend(batch)
        assert enumerator.outcome.stop_reason == EnumStopReason.INTERRUPTED
        assert enumerator.outcome.enum_status == EnumStatus.PARTIAL
        assert len(got) == 1  # first batch already yielded
