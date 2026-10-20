"""Browser lifecycle tests for TwitterContentExtractor.

Covers the four regressions behind the archive crash storm:
1. shared contexts are created per scrape and returned to the manager;
2. the one-off anonymous browser never leaks;
3. a crashed standalone/shared browser is detected and rebuilt;
4. scrape_tweet transparently resets+retries once on TargetClosedError.
"""

import asyncio

import pytest

from ytbot.core import browser_manager as bm_module
# Browser globals (async_playwright, check_and_install_browser) are looked
# up in extractor/browser.py's namespace, so monkeypatch must target that
# module rather than the package re-export.
from ytbot.platforms.twitter.extractor import browser as twitter_module
from ytbot.platforms.twitter import TwitterContentExtractor

CLOSED_ERROR = "Target page, context or browser has been closed"


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ------------------------------------------------------------------ fakes

class FakeResponse:
    def __init__(self, status):
        self.status = status


class FakePage:
    def __init__(self, *, status=200, goto_error=None):
        self.status = status
        self.goto_error = goto_error
        self.closed = False

    async def set_extra_http_headers(self, headers):
        pass

    async def goto(self, url, wait_until=None, timeout=None):
        if self.goto_error is not None:
            raise self.goto_error
        return FakeResponse(self.status)

    async def close(self):
        self.closed = True


class FakeContext:
    def __init__(self, *, page=None, new_page_error=None):
        self.page = page
        self.new_page_error = new_page_error
        self.closed = False
        self.cookies = None

    async def new_page(self):
        if self.new_page_error is not None:
            raise self.new_page_error
        return self.page

    async def add_cookies(self, cookies):
        self.cookies = cookies

    async def add_init_script(self, script):
        pass

    async def close(self):
        self.closed = True


class FakeBrowser:
    def __init__(self, *, connected=True, context=None, fail_close=False):
        self._connected = connected
        self.context = context
        self.fail_close = fail_close
        self.closed = False

    def is_connected(self):
        return self._connected

    async def new_context(self, **kwargs):
        if not self._connected:
            raise RuntimeError(CLOSED_ERROR)
        self.context = self.context or FakeContext()
        return self.context

    async def close(self):
        if self.fail_close:
            raise RuntimeError(CLOSED_ERROR)
        self.closed = True
        self._connected = False


class FakePw:
    """async_playwright() replacement with scripted browsers."""

    def __init__(self, browsers):
        self.browsers = list(browsers)
        self.launches = 0
        self.stops = 0
        self.chromium = self

    def __call__(self):
        return self

    async def start(self):
        return self

    async def launch(self, **kwargs):
        self.launches += 1
        return self.browsers.pop(0)

    async def stop(self):
        self.stops += 1


class FakeSharedManager:
    def __init__(self, *, context_factory=None):
        self.is_initialized = True
        self.created = []
        self.closed = []
        self.resets = 0
        self._context_factory = context_factory or (lambda: FakeContext())

    async def initialize(self):
        return True

    async def create_context(self, cookies=None):
        ctx = self._context_factory()
        self.created.append(ctx)
        return ctx

    async def close_context(self, ctx):
        self.closed.append(ctx)
        await ctx.close()

    async def reset_if_disconnected(self):
        self.resets += 1
        return False


@pytest.fixture
def extractor(monkeypatch):
    ex = TwitterContentExtractor()
    ex._load_twitter_cookies = lambda: []
    monkeypatch.setattr(
        twitter_module, "check_and_install_browser", lambda: None
    )
    return ex


# ------------------------------------------------- shared context lifecycle

class TestSharedContextLifecycle:
    def test_each_initialize_replaces_carried_over_context(
        self, extractor, monkeypatch
    ):
        mgr = FakeSharedManager()

        async def get_mgr():
            return mgr
        monkeypatch.setattr(bm_module, "get_browser_manager", get_mgr)

        run(extractor.initialize_browser())
        first = extractor.context
        run(extractor.initialize_browser())
        second = extractor.context

        assert first is not second
        assert first in mgr.closed
        assert len(mgr.created) == 2
        assert extractor._using_shared_browser is True

    def test_scrape_returns_context_to_manager_in_finally(
        self, extractor, monkeypatch
    ):
        page = FakePage(goto_error=RuntimeError(f"Page.goto: {CLOSED_ERROR}"))
        ctx = FakeContext(page=page)
        mgr = FakeSharedManager()

        async def get_mgr():
            return mgr

        async def fake_init():
            extractor.context = ctx
            extractor._using_shared_browser = True

        extractor.initialize_browser = fake_init
        monkeypatch.setattr(bm_module, "get_browser_manager", get_mgr)

        result = run(extractor._scrape_tweet_once(
            "https://x.com/u/status/1"
        ))

        assert result["success"] is False
        assert CLOSED_ERROR in result["error"]
        assert page.closed is True
        assert mgr.closed == [ctx]
        assert extractor.context is None
        assert extractor._using_shared_browser is False


class TestZombieCleanup:
    def test_dead_standalone_disposed_before_shared_init(
        self, extractor, monkeypatch
    ):
        dead_browser = FakeBrowser(connected=False)
        dead_ctx = FakeContext()
        dead_pw = FakePw([])
        extractor.browser = dead_browser
        extractor.context = dead_ctx
        extractor._playwright = dead_pw
        extractor._using_shared_browser = False

        mgr = FakeSharedManager()

        async def get_mgr():
            return mgr
        monkeypatch.setattr(bm_module, "get_browser_manager", get_mgr)

        run(extractor.initialize_browser())

        assert dead_browser.closed is True
        assert dead_ctx.closed is True
        assert dead_pw.stops == 1
        # Now on the shared path with a live context.
        assert extractor._using_shared_browser is True
        assert extractor.context in mgr.created

    def test_initialize_new_browser_replaces_crashed_instance(
        self, extractor, monkeypatch
    ):
        dead_browser = FakeBrowser(connected=False)
        dead_ctx = FakeContext()
        dead_pw = FakePw([])
        extractor.browser = dead_browser
        extractor.context = dead_ctx
        extractor._playwright = dead_pw

        fresh = FakeBrowser(connected=True)
        new_pw = FakePw([fresh])

        monkeypatch.setattr(
            twitter_module, "async_playwright", lambda: new_pw
        )

        run(extractor._initialize_new_browser())

        assert dead_browser.closed is True
        assert dead_pw.stops == 1
        assert new_pw.launches == 1
        assert extractor.browser is fresh
        assert extractor.context is fresh.context
        assert extractor._ephemeral_browser is False

    def test_reset_browser_shared_path_asks_manager_reset(
        self, extractor, monkeypatch
    ):
        ctx = FakeContext()
        mgr = FakeSharedManager()

        async def get_mgr():
            return mgr
        monkeypatch.setattr(bm_module, "get_browser_manager", get_mgr)
        extractor.context = ctx
        extractor._using_shared_browser = True

        run(extractor.reset_browser())

        assert mgr.resets == 1
        assert extractor.context is None
        assert extractor._using_shared_browser is False


# ----------------------------------------------- crash recovery wrapper

class TestScrapeCrashRecovery:
    def test_retries_once_after_browser_closed_then_succeeds(self, extractor):
        calls = []

        async def fake_once(url):
            calls.append(url)
            if len(calls) == 1:
                return {"success": False,
                        "error": f"Page.wait_for_timeout: {CLOSED_ERROR}"}
            return {"success": True, "content": "ok"}

        resets = []

        async def fake_reset():
            resets.append(1)

        extractor._scrape_tweet_once = fake_once
        extractor.reset_browser = fake_reset

        result = run(extractor.scrape_tweet(
            "https://x.com/u/status/123"
        ))

        assert result["success"] is True
        assert len(calls) == 2
        assert resets == [1]

    def test_second_closed_failure_is_returned(self, extractor):
        async def fake_once(url):
            return {"success": False,
                    "error": f"BrowserContext.new_page: {CLOSED_ERROR}"}

        resets = []

        async def fake_reset():
            resets.append(1)

        extractor._scrape_tweet_once = fake_once
        extractor.reset_browser = fake_reset

        result = run(extractor.scrape_tweet(
            "https://x.com/u/status/123"
        ))

        assert result["success"] is False
        assert CLOSED_ERROR in result["error"]
        assert resets == [1]

    def test_non_browser_error_not_retried(self, extractor):
        calls = []

        async def fake_once(url):
            calls.append(1)
            return {"success": False, "error": "rate limit exceeded"}

        async def fake_reset():
            raise AssertionError("must not reset for non-browser errors")

        extractor._scrape_tweet_once = fake_once
        extractor.reset_browser = fake_reset

        result = run(extractor.scrape_tweet(
            "https://x.com/u/status/123"
        ))

        assert result["error"] == "rate limit exceeded"
        assert len(calls) == 1


# ----------------------------------------------- anonymous one-off browser

class TestAnonymousRetryLifecycle:
    def _patch_manager(self, monkeypatch):
        mgr = FakeSharedManager()

        async def get_instance(cls=None):
            return mgr

        monkeypatch.setattr(
            bm_module.BrowserManager, "get_instance", classmethod(get_instance)
        )
        return mgr

    def test_anonymous_403_tears_down_browser_and_returns_none(
        self, extractor, monkeypatch
    ):
        # Starts from the shared path (as in production scrape_tweet).
        shared_ctx = FakeContext()
        mgr = self._patch_manager(monkeypatch)
        extractor.context = shared_ctx
        extractor._using_shared_browser = True

        anon_page = FakePage(status=403)
        anon_ctx = FakeContext(page=anon_page)
        anon_browser = FakeBrowser(context=anon_ctx)
        pw = FakePw([anon_browser])
        import playwright.async_api as pw_api
        monkeypatch.setattr(pw_api, "async_playwright", lambda: pw)

        result = run(extractor._retry_without_cookies(
            FakePage(), "https://x.com/u/status/9"
        ))

        assert result is None
        # shared context returned to manager, standalone fully torn down
        assert mgr.closed == [shared_ctx]
        assert anon_browser.closed is True
        assert anon_ctx.closed is True
        assert pw.stops == 1
        assert extractor.browser is None
        assert extractor.context is None
        assert extractor._playwright is None
        assert extractor._ephemeral_browser is False

    def test_ephemeral_browser_disposed_in_finally_on_midscrape_crash(
        self, extractor
    ):
        page = FakePage(goto_error=RuntimeError(f"Page.goto: {CLOSED_ERROR}"))
        ctx = FakeContext(page=page)
        browser = FakeBrowser(context=ctx)
        pw = FakePw([])
        extractor.browser = browser
        extractor.context = ctx
        extractor._playwright = pw
        extractor._using_shared_browser = False
        extractor._ephemeral_browser = True

        async def fake_init():
            # Keep the scripted ephemeral state; do not launch anything.
            return None

        extractor.initialize_browser = fake_init

        result = run(extractor._scrape_tweet_once(
            "https://x.com/u/status/5"
        ))

        assert result["success"] is False
        assert browser.closed is True
        assert ctx.closed is True
        assert pw.stops == 1
        assert extractor._ephemeral_browser is False
        assert extractor.browser is None


class TestErrorClassification:
    def test_handler_maps_target_closed_to_browser_closed_code(self):
        from ytbot.platforms.twitter import TwitterHandler

        handler = TwitterHandler()
        code = handler._parse_twitter_error(
            "Page.wait_for_timeout: Target page, context or browser "
            "has been closed"
        )
        assert code == "ERROR_BROWSER_CLOSED"
