"""Twitter/X platform package.

Split out of the former monolithic ``platforms/twitter.py`` (5726 lines):

- ``extractor/`` – browser lifecycle, post detection, content extraction
- ``rendering/``  – HTML / Markdown rendering (pure functions)
- ``media.py``    – image / video downloads
- ``handler.py``  – ``TwitterHandler`` platform interface
"""

from .extractor import (
    PLAYWRIGHT_AVAILABLE,
    TwitterContentExtractor,
    check_and_install_browser,
    ensure_browser_installed,
)
from .handler import (
    TwitterHandler,
    demo_twitter_download,
    register_twitter_platform,
)

__all__ = [
    "TwitterHandler",
    "TwitterContentExtractor",
    "register_twitter_platform",
    "check_and_install_browser",
    "ensure_browser_installed",
    "PLAYWRIGHT_AVAILABLE",
    "demo_twitter_download",
]
