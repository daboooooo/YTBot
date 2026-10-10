"""Outbound proxy resolution for direct HTTP clients.

Playwright/Chrome follow the OS proxy settings automatically, but direct
clients (aiohttp, yt-dlp without explicit options) do not on every
platform. This helper exposes the *effective* proxy for a target URL:

1. Standard environment variables (``HTTPS_PROXY`` / ``HTTP_PROXY`` /
   ``ALL_PROXY`` and their lowercase forms) take precedence.
2. On macOS and Windows ``urllib`` falls back to the OS network settings
   (via ``_scproxy`` / registry), so a Clash-style system proxy on
   127.0.0.1:7897 is picked up without exporting anything.

``NO_PROXY`` and the OS bypass lists are honoured through
``urllib.request.proxy_bypass``.
"""

import urllib.request
from functools import lru_cache
from typing import Optional
from urllib.parse import urlparse


@lru_cache(maxsize=1)
def _system_proxies() -> dict:
    """Proxy mapping (scheme -> proxy URL), cached for the process."""
    try:
        return urllib.request.getproxies()
    except Exception:
        return {}


def clear_proxy_cache() -> None:
    """Drop the cached proxy lookup (used after env/settings changes)."""
    _system_proxies.cache_clear()


def _is_bypassed(hostname: str) -> bool:
    try:
        return urllib.request.proxy_bypass(hostname)
    except Exception:
        return False


def get_system_proxy(url: str = "https://x.com") -> Optional[str]:
    """Return the proxy URL to use for *url*, or ``None`` for a direct
    connection (no proxy configured or host is in the bypass list).

    The returned value is suitable for ``aiohttp``'s ``proxy=`` argument
    and yt-dlp's ``proxy`` option.
    """
    parsed = urlparse(url)
    scheme = (parsed.scheme or "https").lower()
    hostname = parsed.hostname

    if not hostname or _is_bypassed(hostname):
        return None

    proxies = _system_proxies()
    proxy = proxies.get(scheme) or proxies.get("http")
    if proxy and "://" not in proxy:
        proxy = f"http://{proxy}"
    return proxy
