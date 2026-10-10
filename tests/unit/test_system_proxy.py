"""Tests for ytbot.utils.system_proxy."""

import urllib.request

import pytest

from ytbot.utils import system_proxy
from ytbot.utils.system_proxy import clear_proxy_cache, get_system_proxy


@pytest.fixture(autouse=True)
def _reset_cache():
    clear_proxy_cache()
    yield
    clear_proxy_cache()


def test_returns_none_without_any_proxy(monkeypatch):
    monkeypatch.setattr(urllib.request, "getproxies", lambda: {})
    assert get_system_proxy("https://pbs.twimg.com/x.jpg") is None


def test_uses_https_proxy_for_https_url(monkeypatch):
    monkeypatch.setattr(
        urllib.request,
        "getproxies",
        lambda: {"http": "http://h:8080", "https": "http://s:8443"},
    )
    assert (
        get_system_proxy("https://pbs.twimg.com/x.jpg") == "http://s:8443"
    )


def test_falls_back_to_http_proxy(monkeypatch):
    monkeypatch.setattr(
        urllib.request,
        "getproxies",
        lambda: {"http": "http://h:8080"},
    )
    assert (
        get_system_proxy("https://pbs.twimg.com/x.jpg") == "http://h:8080"
    )


def test_normalizes_missing_scheme(monkeypatch):
    monkeypatch.setattr(
        urllib.request,
        "getproxies",
        lambda: {"https": "127.0.0.1:7897"},
    )
    assert (
        get_system_proxy("https://x.com") == "http://127.0.0.1:7897"
    )


def test_default_url_is_https(monkeypatch):
    monkeypatch.setattr(
        urllib.request,
        "getproxies",
        lambda: {"https": "http://s:1"},
    )
    assert get_system_proxy() == "http://s:1"


def test_bypass_list_honoured(monkeypatch):
    monkeypatch.setattr(
        urllib.request,
        "getproxies",
        lambda: {"https": "http://s:1"},
    )
    monkeypatch.setattr(
        urllib.request, "proxy_bypass", lambda host: host == "localhost"
    )
    assert get_system_proxy("https://localhost/api") is None
    assert get_system_proxy("https://x.com") == "http://s:1"


def test_getproxies_error_means_direct(monkeypatch):
    def _boom():
        raise RuntimeError("scutil unavailable")

    monkeypatch.setattr(urllib.request, "getproxies", _boom)
    assert get_system_proxy("https://x.com") is None


def test_proxy_cached_then_cleared(monkeypatch):
    calls = {"n": 0}

    def _getproxies():
        calls["n"] += 1
        return {"https": "http://s:1"}

    monkeypatch.setattr(urllib.request, "getproxies", _getproxies)
    get_system_proxy("https://a.com")
    get_system_proxy("https://b.com")
    assert calls["n"] == 1
    clear_proxy_cache()
    get_system_proxy("https://c.com")
    assert calls["n"] == 2


def test_real_environment_variables_flow_through(monkeypatch):
    # End-to-end through urllib's env reader instead of a stub.
    monkeypatch.setenv("HTTPS_PROXY", "http://env-proxy:9999")
    monkeypatch.setenv("NO_PROXY", "pbs.twimg.com")
    clear_proxy_cache()
    assert get_system_proxy("https://x.com/home") == "http://env-proxy:9999"
    assert get_system_proxy("https://pbs.twimg.com/m.jpg") is None
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.delenv("NO_PROXY", raising=False)


def test_module_exports():
    assert callable(system_proxy.get_system_proxy)
