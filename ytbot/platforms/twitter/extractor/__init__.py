"""Twitter/X content extractor.

``TwitterContentExtractor`` composes the three behavioural mixins:

- :class:`BrowserMixin` – browser lifecycle, cookies, login detection
- :class:`PostsMixin`   – post/thread/video detection, content extraction
- :class:`ContentMixin` – language/expansion helpers, scrape orchestration

Browser-installation helpers are re-exported from :mod:`.browser`.
"""

from typing import Any, Dict, Optional  # noqa: F401

from ..rendering.markdown import (
    convert_to_markdown,
    replace_code_in_markdown,
)
from .browser import (
    PLAYWRIGHT_AVAILABLE,
    BrowserMixin,
    check_and_install_browser,
    ensure_browser_installed,
)
from .content import ContentMixin
from .posts import PostsMixin

# Optional dependencies used only in instance-attribute annotations below
# (local variable annotations are not evaluated at runtime).
try:
    from playwright.async_api import Browser, BrowserContext
except ImportError:  # pragma: no cover
    Browser = None  # type: ignore[assignment]
    BrowserContext = None  # type: ignore[assignment]

aiohttp = None
try:
    import aiohttp
except ImportError:  # pragma: no cover
    pass


class TwitterContentExtractor(BrowserMixin, ContentMixin, PostsMixin):
    """
    Extracts content from Twitter/X using Playwright to bypass anti-bot protection
    """

    # Playwright raises this text on every API call once the browser
    # process crashed/was killed; used to trigger one browser restart.
    BROWSER_CLOSED_HINT = "target page, context or browser has been closed"

    def __init__(self):
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self._playwright = None
        self._using_shared_browser = False
        # True while a one-off standalone browser launched by
        # _retry_without_cookies is in use; scrape_tweet tears it down in
        # its finally so it never leaks into the next scrape.
        self._ephemeral_browser = False
        self._login_checked = False
        self._login_valid = False
        self._http_session: Optional[aiohttp.ClientSession] = None

    def convert_to_markdown(self, content: Dict[str, Any]) -> str:
        """Delegate to rendering.markdown (extracted)."""
        return convert_to_markdown(content)

    def _replace_code_in_markdown(self, markdown: str, full_text: str, placeholder: str) -> str:
        """Delegate to rendering.markdown (extracted)."""
        return replace_code_in_markdown(markdown, full_text, placeholder)

