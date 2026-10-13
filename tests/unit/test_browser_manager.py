"""Unit tests for the shared BrowserManager crash self-healing.

No real browsers: a fake Playwright stack returns FakeBrowser/FakeContext
objects whose connectivity can be scripted (simulating a Chromium
process that died mid-run).
"""

import asyncio

import pytest

from ytbot.core import browser_manager as bm_module
from ytbot.core.browser_manager import BrowserManager


class FakeContext:
    def __init__(self, *, fail_close=False):
        self.closed = False
        self.fail_close = fail_close
        self.init_scripts = []
        self.cookies_added = 0

    async def add_init_script(self, script):
        self.init_scripts.append(script)

    async def add_cookies(self, cookies):
        self.cookies_added += len(cookies)

    async def close(self):
        if self.fail_close:
            raise RuntimeError("Target page, context or browser has been closed")
        self.closed = True


class FakeBrowser:
    def __init__(self, *, connected=True, fail_context=False, fail_close=False):
        self._connected = connected
        self.fail_context = fail_context
        self.fail_close = fail_close
        self.closed = False
        self.contexts = []

    def is_connected(self):
        return self._connected

    def crash(self):
        """Simulate the browser process being killed."""
        self._connected = False

    async def new_context(self, **kwargs):
        if not self._connected or self.fail_context:
            raise RuntimeError(
                "Target page, context or browser has been closed"
            )
        ctx = FakeContext()
        self.contexts.append(ctx)
        return ctx

    async def close(self):
        if self.fail_close:
            raise RuntimeError("already closed")
        self.closed = True
        self._connected = False


class FakeChromium:
    def __init__(self, browsers):
        # browsers: callable() -> FakeBrowser, or list popped in order
        self._browsers = browsers
        self.launch_calls = 0
        self.launch_kwargs = []

    async def launch(self, **kwargs):
        self.launch_calls += 1
        self.launch_kwargs.append(kwargs)
        browser = self._browsers.pop(0) if isinstance(self._browsers, list) \
            else self._browsers()
        browser.launch_order = self.launch_calls
        return browser


class FakePlaywright:
    def __init__(self, browsers):
        self.chromium = FakeChromium(browsers)
        self.start_calls = 0
        self.stop_calls = 0

    def __call__(self):
        # async_playwright() returns an object with start()
        return self

    async def start(self):
        self.start_calls += 1
        return self

    async def stop(self):
        self.stop_calls += 1


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture
def manager(monkeypatch):
    """Fresh singleton per test; fake playwright installed lazily via
    manager.attach-style attribute set by each test."""
    BrowserManager._instance = None
    monkeypatch.setattr(bm_module, "_browser_manager", None)
    mgr = BrowserManager()
    yield mgr
    BrowserManager._instance = None
    monkeypatch.setattr(bm_module, "_browser_manager", None)


def install_playwright(monkeypatch, browsers):
    pw = FakePlaywright(browsers)
    monkeypatch.setattr(bm_module, "async_playwright", pw)
    return pw


class TestInitializeLiveness:
    def test_healthy_browser_launched_once(self, manager, monkeypatch):
        browser = FakeBrowser()
        pw = install_playwright(monkeypatch, [browser])

        assert run(manager.initialize()) is True
        assert run(manager.initialize()) is True

        assert pw.chromium.launch_calls == 1
        assert manager.is_initialized is True

    def test_disconnected_browser_is_rebuilt(self, manager, monkeypatch):
        dead = FakeBrowser(connected=True)
        fresh = FakeBrowser()
        pw = install_playwright(monkeypatch, [dead, fresh])

        assert run(manager.initialize()) is True
        dead.crash()
        # Previously: object still referenced -> "healthy", no relaunch.
        assert manager.is_initialized is False

        assert run(manager.initialize()) is True

        assert pw.chromium.launch_calls == 2
        assert dead.closed is True
        assert manager.is_initialized is True

    def test_failed_launch_can_be_retried(self, manager, monkeypatch):
        class FailingChromium(FakeChromium):
            async def launch(self, **kwargs):
                self.launch_calls += 1
                if self.launch_calls == 1:
                    raise RuntimeError("chromium failed to start")
                browser = self._browsers.pop(0)
                return browser

        pw = FakePlaywright([FakeBrowser()])
        pw.chromium = FailingChromium(pw.chromium._browsers)
        monkeypatch.setattr(bm_module, "async_playwright", pw)

        assert run(manager.initialize()) is False
        # Old code set a one-way shutdown latch here; retry must work.
        assert run(manager.initialize()) is True
        assert pw.chromium.launch_calls == 2


class TestContextLifecycle:
    def test_context_closed_via_manager(self, manager, monkeypatch):
        install_playwright(monkeypatch, [FakeBrowser()])
        run(manager.initialize())

        ctx = run(manager.create_context())
        assert ctx is not None
        run(manager.close_context(ctx))
        assert ctx.closed is True

    def test_create_context_on_dead_browser_disposes_and_returns_none(
        self, manager, monkeypatch
    ):
        browser = FakeBrowser()
        pw = install_playwright(monkeypatch, [browser, FakeBrowser()])
        run(manager.initialize())

        browser.crash()
        ctx = run(manager.create_context())
        assert ctx is None
        assert browser.closed is True
        # Next initialize relaunches instead of reusing the zombie.
        assert manager.is_initialized is False
        assert run(manager.initialize()) is True
        assert pw.chromium.launch_calls == 2

    def test_context_failure_on_healthy_browser_keeps_browser(
        self, manager, monkeypatch
    ):
        browser = FakeBrowser(fail_context=True)
        pw = install_playwright(monkeypatch, [browser])
        run(manager.initialize())

        ctx = run(manager.create_context())
        assert ctx is None
        # Browser itself reachable: must not be torn down.
        assert manager.is_initialized is True
        assert browser.closed is False
        assert pw.chromium.launch_calls == 1


class TestResetIfDisconnected:
    def test_healthy_returns_true_without_dispose(self, manager, monkeypatch):
        browser = FakeBrowser()
        install_playwright(monkeypatch, [browser])
        run(manager.initialize())

        assert run(manager.reset_if_disconnected()) is True
        assert browser.closed is False

    def test_dead_browser_disposed_without_shutdown_latch(
        self, manager, monkeypatch
    ):
        browser = FakeBrowser()
        pw = install_playwright(monkeypatch, [browser, FakeBrowser()])
        run(manager.initialize())
        browser.crash()

        assert run(manager.reset_if_disconnected()) is False
        assert browser.closed is True
        # Unlike shutdown(), a fresh initialize() must be allowed.
        assert run(manager.initialize()) is True
        assert pw.chromium.launch_calls == 2

    def test_explicit_shutdown_blocks_reinitialize(self, manager, monkeypatch):
        install_playwright(monkeypatch, [FakeBrowser(), FakeBrowser()])
        run(manager.initialize())

        run(manager.shutdown())

        assert manager.is_initialized is False
        assert run(manager.initialize()) is False
