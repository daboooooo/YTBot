"""
Twitter/X platform handler for YTBot

Implements complete Twitter/X content extraction with:
- Playwright-based scraping to bypass anti-bot protection
- Automatic long tweet expansion
- Content filtering (analytics, ads, recommendations)
- Markdown formatting with preserved formatting (bold, links, code, italic)
- Media download support
- Integration with storage service
"""

import asyncio
import re
import shutil
import tempfile
import os
from typing import Dict, Any, Optional, List
from datetime import datetime
from urllib.parse import urlparse, urlunparse

from ytbot.platforms.base import PlatformHandler, ContentInfo, ContentType, DownloadResult
from ytbot.core.enhanced_logger import get_logger
from ytbot.services.storage_service import StorageService
from ytbot.services.pdf_converter import pdf_converter

# Try to import aiohttp for link preview
aiohttp = None
try:
    import aiohttp
except ImportError:
    pass

logger = get_logger(__name__)

# Try to import playwright, provide helpful error if not available
try:
    from playwright.async_api import async_playwright, Browser, BrowserContext
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    logger.warning(
        "Playwright not installed. Twitter/X content extraction will not work. "
        "Install with: pip install playwright && playwright install chromium"
    )


def ensure_browser_installed() -> bool:
    """
    Check if Playwright browser is installed, install if not.
    Returns True if browser is available (either already installed or just installed).
    """
    if not PLAYWRIGHT_AVAILABLE:
        return False

    try:
        with sync_playwright() as p:
            try:
                p.chromium.executable_path
                return True
            except Exception:
                pass
    except Exception:
        pass

    logger.info("Playwright browser not found. Installing chromium...")
    import subprocess
    import sys

    try:
        result = subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            capture_output=True,
            text=True,
            timeout=300
        )
        if result.returncode == 0:
            logger.info("Playwright chromium browser installed successfully")
            return True
        else:
            logger.error(f"Failed to install Playwright browser: {result.stderr}")
            return False
    except subprocess.TimeoutExpired:
        logger.error("Timeout while installing Playwright browser")
        return False
    except Exception as e:
        logger.error(f"Error installing Playwright browser: {e}")
        return False


_BROWSER_CHECKED = False


def check_and_install_browser():
    """Check and install browser once per session."""
    global _BROWSER_CHECKED
    if not _BROWSER_CHECKED:
        _BROWSER_CHECKED = True
        if PLAYWRIGHT_AVAILABLE and not ensure_browser_installed():
            logger.warning(
                "Playwright browser installation failed. "
                "Twitter/X content extraction may not work. "
                "Try running manually: playwright install chromium"
            )


class TwitterContentExtractor:
    """
    Extracts content from Twitter/X using Playwright to bypass anti-bot protection
    """

    def __init__(self):
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self._playwright = None
        self._using_shared_browser = False
        self._login_checked = False
        self._login_valid = False
        self._http_session: Optional[aiohttp.ClientSession] = None

    @staticmethod
    def _clean_tweet_url(url: str) -> str:
        parsed = urlparse(url)
        clean = urlunparse((parsed.scheme, parsed.netloc, parsed.path, '', '', ''))
        return clean

    def _load_twitter_cookies(self) -> List[Dict[str, Any]]:
        """
        加载 Twitter/X cookies

        支持三种方式（按优先级）:
        1. 从项目根目录的 .twitter_cookies.json 文件加载
        2. 从 TWITTER_COOKIES_FILE 环境变量指定的文件加载
        3. 从 TWITTER_COOKIES_JSON 环境变量直接读取

        Returns:
            List of cookie dicts compatible with Playwright
        """
        import json
        import time
        from ytbot.core.config import get_config

        config = get_config()
        twitter_config = config.twitter
        TWITTER_COOKIES_FILE = twitter_config.cookies_file
        TWITTER_COOKIES_JSON = twitter_config.cookies_json

        cookies = []

        # 方式0: 从项目根目录的 .twitter_cookies.json 加载（优先级最高）
        default_cookie_file = '.twitter_cookies.json'
        if os.path.exists(default_cookie_file):
            try:
                with open(default_cookie_file, 'r', encoding='utf-8-sig') as f:
                    raw = f.read()
                json_start = raw.find('[')
                if json_start < 0:
                    json_start = raw.find('{')
                if json_start > 0:
                    logger.warning(
                        f"Skipping {json_start} bytes of non-JSON prefix in {default_cookie_file}"
                    )
                    raw = raw[json_start:]
                elif json_start < 0:
                    raw = ''
                cookie_data = json.loads(raw) if raw else []
                if isinstance(cookie_data, list):
                    cookies = cookie_data
                    logger.info(
                        f"Loaded {len(cookies)} cookies from default file: "
                        f"{default_cookie_file}"
                    )
                else:
                    logger.warning("Invalid cookie file format")
            except Exception as e:
                logger.error(f"Failed to load cookies from default file: {e}")

        # 方式1: 从 TWITTER_COOKIES_FILE 环境变量指定的文件加载
        elif TWITTER_COOKIES_FILE and os.path.exists(TWITTER_COOKIES_FILE):
            try:
                with open(TWITTER_COOKIES_FILE, 'r', encoding='utf-8') as f:
                    cookie_data = json.load(f)
                    # 支持两种格式:
                    # 1. Playwright 格式: [{name, value, domain, path, ...}]
                    # 2. EditThisCookie 格式: [{name, value, domain, path, ...}]
                    if isinstance(cookie_data, list):
                        cookies = cookie_data
                        logger.info(
                            f"Loaded {len(cookies)} cookies from file: "
                            f"{TWITTER_COOKIES_FILE}"
                        )
                    else:
                        logger.warning("Invalid cookie file format")
            except Exception as e:
                logger.error(f"Failed to load cookies from file: {e}")

        # 方式2: 从环境变量加载
        elif TWITTER_COOKIES_JSON:
            try:
                cookie_data = json.loads(TWITTER_COOKIES_JSON)
                if isinstance(cookie_data, list):
                    cookies = cookie_data
                    logger.info(f"Loaded {len(cookies)} cookies from env var")
                else:
                    logger.warning("Invalid cookie JSON format in env var")
            except Exception as e:
                logger.error(f"Failed to parse cookies from env var: {e}")

        # 检查 cookie 过期时间
        current_time = time.time()
        expired_cookies = []
        valid_cookies = []
        
        for cookie in cookies:
            if isinstance(cookie, dict) and 'name' in cookie and 'value' in cookie:
                # 检查过期时间
                expires = cookie.get('expires')
                if expires:
                    # 如果过期时间是时间戳（数字）
                    if isinstance(expires, (int, float)):
                        if expires < current_time:
                            expired_cookies.append(cookie['name'])
                            continue
                
                valid_cookies.append(cookie)
        
        if expired_cookies:
            logger.warning(f"Found {len(expired_cookies)} expired cookies: {expired_cookies}")
        
        logger.info(f"Valid cookies: {len(valid_cookies)}")

        # 确保 cookies 格式正确 (Playwright 格式)
        formatted_cookies = []
        for cookie in valid_cookies:
            formatted_cookie = {
                'name': cookie['name'],
                'value': cookie['value'],
                'domain': cookie.get('domain', '.x.com'),
                'path': cookie.get('path', '/'),
            }
            # 可选字段
            if 'expires' in cookie:
                formatted_cookie['expires'] = cookie['expires']
            if 'httpOnly' in cookie:
                formatted_cookie['httpOnly'] = cookie['httpOnly']
            if 'secure' in cookie:
                formatted_cookie['secure'] = cookie['secure']
            if 'sameSite' in cookie:
                # 转换 sameSite 值为 Playwright 支持的格式
                same_site = cookie['sameSite']
                if same_site == 'no_restriction':
                    formatted_cookie['sameSite'] = 'None'
                elif same_site == 'lax':
                    formatted_cookie['sameSite'] = 'Lax'
                elif same_site == 'strict':
                    formatted_cookie['sameSite'] = 'Strict'
                elif same_site in ['Strict', 'Lax', 'None']:
                    formatted_cookie['sameSite'] = same_site
                # 如果值不匹配任何已知格式，不添加 sameSite

            formatted_cookies.append(formatted_cookie)

        return formatted_cookies

    async def _check_login_status(self, page) -> Dict[str, Any]:
        """
        检查 Twitter/X 登录状态
        
        Returns:
            Dict with 'is_logged_in' (bool) and 'error_message' (str) if not logged in
        """
        result = await page.evaluate("""
            () => {
                // 检查登录状态的多种方式
                
                // 1. 检查是否有登录按钮
                const loginButton = document.querySelector('[data-testid="loginButton"]');
                const signInLink = document.querySelector('a[href="/login"]');
                
                // 2. 检查页面文本中是否有登录相关提示
                const pageText = document.body.innerText;
                const loginPrompts = [
                    '登录',
                    '注册',
                    'Log in',
                    'Sign up',
                    '出错了',
                    '重新加载',
                    '登录注册出错了'
                ];
                
                let hasLoginPrompt = false;
                for (const prompt of loginPrompts) {
                    if (pageText.includes(prompt)) {
                        hasLoginPrompt = true;
                        break;
                    }
                }
                
                // 3. 检查是否有用户头像（登录后通常有）
                const userAvatar = document.querySelector(
                    '[data-testid="primaryColumn"] img[src*="profile"], '
                    + '[data-testid="SideNav_AccountSwitcher_Button"] img'
                );
                
                // 4. 检查页面标题
                const pageTitle = document.title;
                const isErrorPage = pageTitle.includes('错误') || 
                                   pageTitle.includes('Error') ||
                                   pageTitle === 'X';
                const isEmptyPage = pageTitle === '';
                
                // 5. 检查是否有内容区域
                const contentArea = document.querySelector('[data-testid="primaryColumn"]');
                const hasContent = contentArea && contentArea.innerText.length > 100;
                
                // 判断逻辑
                // 空页面标题可能是加载未完成，不直接判定为错误页面
                // 只有在有明确错误标识时才判定为未登录
                const isLoggedIn = userAvatar || (hasContent && !hasLoginPrompt && !isErrorPage);
                
                let errorMessage = '';
                if (!isLoggedIn) {
                    if (hasLoginPrompt) {
                        errorMessage = '需要登录才能查看此内容，请更新 cookies';
                    } else if (isErrorPage) {
                        errorMessage = '页面加载错误，可能需要重新登录';
                    } else if (isEmptyPage && !hasContent) {
                        errorMessage = '页面未正常加载，可能需要重新登录';
                    } else {
                        errorMessage = '无法确认登录状态';
                    }
                }
                
                return {
                    is_logged_in: isLoggedIn,
                    has_login_prompt: hasLoginPrompt,
                    has_user_avatar: !!userAvatar,
                    has_content: hasContent,
                    is_error_page: isErrorPage,
                    page_title: pageTitle,
                    error_message: errorMessage
                };
            }
        """)
        
        if not result.get('is_logged_in'):
            logger.warning(f"Twitter/X login check failed: {result.get('error_message')}")
            logger.warning(f"Page title: {result.get('page_title')}")
        else:
            logger.info("Twitter/X login status: OK")
            
        return result

    async def _wait_for_content(self, page, timeout: int = 15000):
        logger.info("Waiting for page content to load...")
        try:
            await page.wait_for_selector('article', timeout=timeout)
        except Exception:
            logger.warning(
                "Timeout waiting for article element, continuing anyway"
            )
        await page.wait_for_timeout(5000)

    async def _retry_without_cookies(self, page, url: str):
        logger.info("Retrying without cookies (anonymous access)")
        try:
            await page.close()
        except Exception:
            pass

        await self.close_browser()

        pw = None
        try:
            from playwright.async_api import async_playwright
            pw = await async_playwright().start()
            self.browser = await pw.chromium.launch(
                headless=True,
                args=[
                    '--no-sandbox',
                    '--disable-setuid-sandbox',
                    '--disable-dev-shm-usage',
                    '--no-first-run',
                    '--no-zygote',
                    '--disable-gpu',
                ]
            )
            user_agent = (
                'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/131.0.0.0 Safari/537.36'
            )
            self.context = await self.browser.new_context(
                user_agent=user_agent,
                viewport={'width': 1920, 'height': 1080},
                timezone_id='Asia/Shanghai',
                locale='zh-CN,zh;q=0.9,en;q=0.8',
            )
            await self.context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined,
                });
                Object.defineProperty(navigator, 'languages', {
                    get: () => ['zh-CN', 'zh', 'en-US', 'en'],
                });
                Object.defineProperty(navigator, 'platform', {
                    get: () => 'MacIntel',
                });
                window.chrome = {
                    runtime: {},
                    loadTimes: function(){},
                    csi: function(){},
                    app: {},
                };
            """)
            self._using_shared_browser = False
            self._playwright = pw

            new_page = await self.context.new_page()
            await new_page.set_extra_http_headers({
                'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            })

            resp = await new_page.goto(
                url, wait_until='domcontentloaded', timeout=90000
            )
            status = resp.status if resp else 'no response'
            logger.info(f"Anonymous access returned: {status}")

            if status == 200:
                await self._wait_for_content(new_page)
                login_status = await self._check_login_status(new_page)
                if login_status.get('has_content'):
                    logger.info(
                        "Anonymous access succeeded, "
                        "cookies were invalid"
                    )
                    return new_page
                else:
                    logger.warning(
                        "Anonymous access returned 200 but no content"
                    )
            else:
                logger.warning(
                    f"Anonymous access also failed: {status}"
                )

            return new_page

        except Exception as e:
            logger.error(f"Error during anonymous retry: {e}")
            raise

    async def initialize_browser(self, force_new: bool = False):
        """Initialize Playwright browser with anti-detection measures.
        
        Args:
            force_new: If True, always create a new browser instance instead of using shared one
        """
        if not PLAYWRIGHT_AVAILABLE:
            raise RuntimeError(
                "Playwright not installed. "
                "Install with: pip install playwright && playwright install chromium"
            )

        if force_new:
            await self._initialize_new_browser()
            return

        try:
            from ytbot.core.browser_manager import get_browser_manager
            manager = await get_browser_manager()
            
            if not manager.is_initialized:
                initialized = await manager.initialize()
                if not initialized:
                    logger.warning(
                        "Failed to initialize shared browser, "
                        "falling back to new instance"
                    )
                    await self._initialize_new_browser()
                    return
            
            cookies = self._load_twitter_cookies()
            context = await manager.create_context(cookies=cookies)
            
            if context:
                self.context = context
                self._using_shared_browser = True
                logger.info("Using shared browser for Twitter/X extraction")
            else:
                logger.warning(
                    "Failed to create shared browser context, "
                    "falling back to new instance"
                )
                await self._initialize_new_browser()
                
        except ImportError:
            logger.warning("Browser manager not available, using standalone browser")
            await self._initialize_new_browser()
        except Exception as e:
            logger.warning(f"Error using shared browser: {e}, falling back to new instance")
            await self._initialize_new_browser()

    async def _initialize_new_browser(self):
        """Initialize a new standalone browser instance"""
        check_and_install_browser()

        if self.browser is None:
            self._playwright = await async_playwright().start()

            # Launch browser with anti-detection settings
            self.browser = await self._playwright.chromium.launch(
                headless=True,
                args=[
                    '--no-sandbox',
                    '--disable-setuid-sandbox',
                    '--disable-dev-shm-usage',
                    '--disable-accelerated-2d-canvas',
                    '--no-first-run',
                    '--no-zygote',
                    '--disable-gpu',
                    '--disable-web-security',
                    '--allow-running-insecure-content',
                    '--disable-features=VizDisplayCompositor',
                    '--disable-extensions',
                    '--disable-ipc-flooding-protection',
                    '--disable-background-timer-throttling',
                    '--disable-backgrounding-occluded-windows',
                    '--disable-renderer-backgrounding',
                ]
            )

            # Create context with realistic settings
            user_agent = (
                'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
                '(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'
            )
            self.context = await self.browser.new_context(
                user_agent=user_agent,
                viewport={'width': 1920, 'height': 1080},
                timezone_id='Asia/Shanghai',
                locale='zh-CN,zh;q=0.9,en;q=0.8',
                geolocation={'longitude': 121.4737, 'latitude': 31.2304},
                permissions=['geolocation']
            )

            # 加载并添加 cookies
            cookies = self._load_twitter_cookies()
            if cookies:
                await self.context.add_cookies(cookies)
                logger.info(f"Added {len(cookies)} cookies to browser context")

            # Inject anti-detection scripts
            await self.context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined,
                });

                Object.defineProperty(navigator, 'plugins', {
                    get: () => ({
                        length: 5,
                        0: { filename: 'internal-pdf-viewer',
                             name: 'Chrome PDF Plugin',
                             description: 'Portable Document Format' },
                        1: { filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai',
                             name: 'Chrome PDF Viewer',
                             description: '' },
                        2: { filename: 'internal-nacl-plugin',
                             name: 'Native Client',
                             description: '' },
                        3: { filename: 'widevinecdmadapter',
                             name: 'Widevine Content Decryption Module',
                             description: 'Enables Widevine licenses for DRM content' },
                        4: { filename: 'oehmokhphbnpkaceddhaklhamklcpgec',
                             name: 'CryptoTokenExtension',
                             description: 'CryptoToken Extension' },
                        refresh: () => {},
                    }),
                });

                Object.defineProperty(navigator, 'languages', {
                    get: () => ['zh-CN', 'zh', 'en-US', 'en'],
                });

                Object.defineProperty(navigator, 'platform', {
                    get: () => 'MacIntel',
                });

                Object.defineProperty(navigator, 'hardwareConcurrency', {
                    get: () => 8,
                });

                Object.defineProperty(navigator, 'deviceMemory', {
                    get: () => 8,
                });

                window.chrome = {
                    runtime: {},
                    loadTimes: function() {},
                    csi: function() {},
                    app: {},
                };

                Object.defineProperty(navigator, 'connection', {
                    get: () => ({
                        effectiveType: '4g',
                        rtt: 50,
                        downlink: 10,
                        saveData: false,
                    }),
                });

                const originalQuery = window.navigator.permissions.query;
                window.navigator.permissions.query = (parameters) => (
                    parameters.name === 'notifications' ?
                        Promise.resolve({ state: Notification.permission }) :
                        originalQuery(parameters)
                );
            """)

    async def close_browser(self):
        """Close the browser instance and cleanup resources"""
        if self._using_shared_browser:
            # Properly close the shared browser context to prevent memory leak
            from ..core.browser_manager import BrowserManager
            browser_mgr = await BrowserManager.get_instance()
            if self.context:
                await browser_mgr.close_context(self.context)
            self.context = None
            self._using_shared_browser = False
            return

        if self.browser:
            await self.browser.close()
            self.browser = None
        if self.context:
            await self.context.close()
            self.context = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None

        # Close the shared HTTP session
        await self.close_http_session()

    async def get_http_session(self) -> 'aiohttp.ClientSession':
        """Get or create a reusable aiohttp ClientSession."""
        if self._http_session is None or self._http_session.closed:
            self._http_session = aiohttp.ClientSession()
        return self._http_session

    async def close_http_session(self):
        """Close the reusable aiohttp ClientSession."""
        if self._http_session and not self._http_session.closed:
            await self._http_session.close()
            self._http_session = None

    async def _switch_to_original_language(self, page) -> bool:
        """
        Ensure tweet content is displayed in its original language.

        X/Twitter auto-translates tweets when the user's locale
        differs from the tweet's language. This method detects
        auto-translation and clicks 'View original' to restore
        the original text, ensuring we always get the authentic
        content rather than a machine translation.

        Returns:
            bool - Whether the original language was restored
        """
        try:
            translation_indicator = await page.query_selector(
                '[data-testid="translation"]'
            )

            if not translation_indicator:
                logger.debug("No auto-translation detected on page")
                return False

            logger.info(
                "Auto-translation detected, "
                "switching to original language"
            )

            view_original_selectors = [
                '[data-testid="translation"] a',
                '[data-testid="translation"] [role="button"]',
                '[data-testid="translation"] span[role="button"]',
            ]

            for selector in view_original_selectors:
                btn = await page.query_selector(selector)
                if btn:
                    try:
                        await btn.click()
                        await page.wait_for_timeout(1500)
                        logger.info(
                            "Switched to original language successfully"
                        )
                        return True
                    except Exception:
                        continue

            hidden = await page.evaluate("""
                () => {
                    const translations = document.querySelectorAll(
                        '[data-testid="translation"]'
                    );
                    translations.forEach(el => {
                        el.style.display = 'none';
                    });

                    const tweetText = document.querySelector(
                        '[data-testid="tweetText"]'
                    );
                    if (tweetText) {
                        const langSpans = tweetText.querySelectorAll(
                            'span[lang]'
                        );
                        langSpans.forEach(span => {
                            span.style.display = '';
                        });
                    }

                    return translations.length > 0;
                }
            """)

            if hidden:
                logger.info("Hidden translation overlay via JavaScript")
                return True

            logger.debug("No translation found, keeping current content")
            return False

        except Exception as e:
            logger.debug(f"Error switching to original language: {e}")
            return False

    async def expand_long_tweet(self, page) -> bool:
        """Click 'Show more' button to expand long tweets"""
        expanded = False
        max_attempts = 3  # 减少尝试次数

        for attempt in range(max_attempts):
            found_button = False
            try:
                # 只使用 data-testid 选择器，更精确
                btn = await page.query_selector('[data-testid="tweet-text-show-more-link"]')

                if btn:
                    await btn.click()
                    await page.wait_for_timeout(1500)
                    logger.info(
                        f"Expanded long tweet content (attempt {attempt + 1})"
                    )
                    expanded = True
                    found_button = True

                if not found_button:
                    break

            except Exception as e:
                logger.debug(f"Expand attempt {attempt + 1} failed: {e}")
                continue

        return expanded

    async def expand_thread_replies(self, page) -> bool:
        """
        点击"查看回复"按钮展开连续贴内容，并滚动加载更多内容

        X/Twitter 在未登录状态下不会自动显示连续贴，
        需要点击"查看 X 条回复"按钮才能看到完整线程

        Returns:
            bool - 是否成功展开回复
        """
        expanded = False
        max_attempts = 3

        for attempt in range(max_attempts):
            try:
                # 查找"查看回复"或"Show replies"按钮
                btn_info = await page.evaluate("""
                    () => {
                        const buttons = document.querySelectorAll(
                            'div[role="button"], span, a'
                        );
                        for (const btn of buttons) {
                            const text = btn.textContent.trim();
                            // 匹配"查看 X 条回复"或"Show X replies"
                            // 注意：不匹配"查看引用"/"Show quotes"，点击引用会导航离开当前推文
                            if (text.match(/查看.*回复/) ||
                                text.match(/Show.*repl/i)) {
                                return {
                                    found: true,
                                    text: text,
                                    element: btn.tagName
                                };
                            }
                        }
                        return { found: false };
                    }
                """)

                if btn_info.get('found'):
                    logger.info(f"Found reply button: {btn_info.get('text')}")

                    # 尝试点击包含"查看"和"回复"文本的元素
                    try:
                        # 先尝试通过文本查找
                        btn = await page.query_selector(
                            'text=/查看.*回复|Show.*repl/i'
                        )
                        if btn:
                            await btn.click()
                            await page.wait_for_timeout(3000)
                            logger.info(
                                f"Clicked reply button (attempt {attempt + 1})"
                            )
                            expanded = True

                            # 等待内容加载后再次检查是否有更多回复
                            await page.wait_for_timeout(2000)
                        else:
                            break
                    except Exception as click_err:
                        logger.debug(f"Click failed: {click_err}")
                        break
                else:
                    break

            except Exception as e:
                logger.debug(f"Expand replies attempt {attempt + 1} failed: {e}")
                break
        
        # 滚动加载更多内容（连续贴）
        try:
            logger.info("Scrolling to load more thread content...")
            previous_post_count = 0
            scroll_attempts = 0
            max_scroll_attempts = 5
            
            while scroll_attempts < max_scroll_attempts:
                # 获取当前帖子数量
                current_post_count = await page.evaluate(
                    '() => document.querySelectorAll(\'article[data-testid="tweet"]\').length'
                )
                
                if current_post_count > previous_post_count:
                    logger.info(f"Loaded {current_post_count} posts so far")
                    previous_post_count = current_post_count
                    expanded = True
                
                # 滚动页面
                await page.evaluate('window.scrollBy(0, 800)')
                await page.wait_for_timeout(2000)
                
                scroll_attempts += 1
            
            # 滚动回顶部
            await page.evaluate('window.scrollTo(0, 0)')
            await page.wait_for_timeout(1000)
            
        except Exception as e:
            logger.debug(f"Scroll loading failed: {e}")

        return expanded

    async def extract_author_and_time(self, page) -> Dict[str, str]:
        """
        Extract author username and publish time from the tweet page.

        Args:
            page: Playwright page object

        Returns:
            Dict with 'author' (username with @) and 'timestamp' (ISO format)
        """
        result = await page.evaluate("""
            () => {
                let author = '';
                let timestamp = '';

                // Try to extract author from various selectors
                const authorSelectors = [
                    'a[href^="/"] > div > span',
                    'a[role="link"] span',
                    '[data-testid="User-Name"] a',
                    'article a[href^="/"]',
                    'div[data-testid="User-Name"] a',
                    'a[href*="/status/"]',
                ];

                for (const selector of authorSelectors) {
                    const elements = document.querySelectorAll(selector);
                    for (const el of elements) {
                        const text = el.textContent.trim();
                        const href = el.getAttribute('href') || '';
                        // Check if it looks like a username
                        if (text && (
                            text.startsWith('@') ||
                            href.match('/^\\\\/' + '[\\\\w_]+$')
                        )) {
                            if (text.startsWith('@')) {
                                author = text;
                            } else if (href) {
                                author = '@' + href.replace('/', '');
                            }
                            break;
                        }
                    }
                    if (author) break;
                }

                // Fallback: extract from URL if available
                if (!author) {
                    const urlMatch = window.location.pathname.match(
                        '/\\\\/(\\\\w+)\\\\/status\\\\/'
                    );
                    if (urlMatch) {
                        author = '@' + urlMatch[1];
                    }
                }

                // Try to extract timestamp from various selectors
                const timeSelectors = [
                    'time',
                    'time[datetime]',
                    '[data-testid="tweet"] time',
                    'article time',
                    'a[href*="/status/"] time',
                ];

                for (const selector of timeSelectors) {
                    const timeEl = document.querySelector(selector);
                    if (timeEl) {
                        // Try datetime attribute first
                        const datetime = timeEl.getAttribute('datetime');
                        if (datetime) {
                            timestamp = datetime;
                            break;
                        }
                        // Fallback to text content
                        const text = timeEl.textContent.trim();
                        if (text) {
                            timestamp = text;
                            break;
                        }
                    }
                }

                return {
                    author: author || 'Unknown',
                    timestamp: timestamp || ''
                };
            }
        """)

        author = result.get('author')
        timestamp = result.get('timestamp')
        logger.info(
            f"Extracted author: {author}, timestamp: {timestamp}"
        )
        return result

    async def detect_post_type(self, page) -> Dict[str, Any]:
        """
        Detect if the post is a long article or regular tweet

        Returns:
            Dict with 'post_type' ('article' or 'regular') and 'article_title' if applicable
        """
        result = await page.evaluate("""
            () => {
                let articleTitle = '';

                // 优先从页面标题中提取（最可靠的方式）
                // 英文格式: "(1) 作者 on X: "标题内容" / X"
                // 中文格式: "(1) X 上的 作者："标题内容" / X"
                // 注意：中文使用全角引号 ""，英文使用半角引号 ""
                const pageTitle = document.title;
                let pageTitleContent = '';

                // 英文格式匹配 (on X: "...")
                if (pageTitle && pageTitle.includes(' on X:')) {
                    const titleMatch = pageTitle.match(/on X:\\s*"([^"]+)"/);
                    if (titleMatch && titleMatch[1]) {
                        pageTitleContent = titleMatch[1].trim();
                    }
                }

                // 中文格式匹配 (X 上的 作者："...")
                if (!pageTitleContent && pageTitle &&
                    pageTitle.includes('X 上的')) {
                    const titleMatch = pageTitle.match(
                        /X 上的[^：]+[：:]["\u201c]([^\u201d"]+)[\u201d"]/
                    );
                    if (titleMatch && titleMatch[1]) {
                        pageTitleContent = titleMatch[1].trim();
                    }
                }

                // 通用格式匹配（支持全角和半角引号）
                if (!pageTitleContent && pageTitle) {
                    const titleMatch = pageTitle.match(
                        /[：:]["\u201c]([^\u201d"]+)[\u201d"]/
                    );
                    if (titleMatch && titleMatch[1] &&
                        titleMatch[1].length > 5) {
                        pageTitleContent = titleMatch[1].trim();
                    }
                }

                if (pageTitleContent) {
                    // 从完整内容中提取标题部分
                    // 优先按句号/感叹号/问号分割，取第一句作为标题
                    const sentenceEnd = pageTitleContent.search(/[。！？]/);
                    if (sentenceEnd > 0) {
                        articleTitle = pageTitleContent.substring(0, sentenceEnd + 1);
                    } else {
                        // 没有句号分隔时，尝试按逗号分割（中文标题常用逗号）
                        const commaEnd = pageTitleContent.search(/[,，]/);
                        if (commaEnd > 0 && commaEnd <= 100) {
                            articleTitle = pageTitleContent.substring(0, commaEnd);
                        } else {
                            // 没有合适的标点分隔时，尝试按空格分割
                            const spaceIdx = pageTitleContent.indexOf(' ', 10);
                            if (spaceIdx > 10) {
                                articleTitle = pageTitleContent.substring(0, spaceIdx);
                            } else {
                                // 直接使用完整内容作为标题
                                articleTitle = pageTitleContent;
                            }
                        }
                    }
                }

                // 如果从页面标题没有提取到，尝试从 DOM 元素提取
                if (!articleTitle) {
                    const articleTitleSelectors = [
                        '[data-testid="articleTitle"]',
                        '[data-testid="tweetTitle"]',
                        'article h2',
                        'article h1',
                        '[role="article"] h2',
                        '[role="article"] h1',
                        'div[data-testid="cellInnerDiv"] h2',
                        'div[data-testid="cellInnerDiv"] h1',
                    ];

                    const placeholders = ['Article', 'Post', 'Conversation',
                        '文章', '帖子', '对话'];

                    for (const selector of articleTitleSelectors) {
                        const element = document.querySelector(selector);
                        if (element) {
                            const text = element.textContent.trim();
                            if (text && text.length > 0 && text.length < 500
                                && !placeholders.includes(text)) {
                                articleTitle = text;
                                break;
                            }
                        }
                    }
                }

                // Check for article-specific page structure
                const hasRichTextView = !!document.querySelector(
                    '[data-testid="twitterArticleRichTextView"]'
                );
                let hasArticleStructure = !!document.querySelector('article');

                // Check for "Article" badge/label in the page
                let hasArticleLabel = false;
                const pageText = document.body.innerText.toLowerCase();
                if (pageText.includes('article') ||
                    pageText.includes('文章') ||
                    document.title.toLowerCase().includes('article')) {
                    hasArticleLabel = true;
                }

                // Get tweet text content for length check
                const tweetTextSelectors = [
                    '[data-testid="tweetText"]',
                    'article [lang]',
                    '[data-testid="tweet"] div[dir="auto"]',
                    'article div[data-testid="tweetText"]'
                ];

                let tweetText = '';
                for (const selector of tweetTextSelectors) {
                    const elements = document.querySelectorAll(selector);
                    for (const element of elements) {
                        const text = element.textContent.trim();
                        if (text && text.length > tweetText.length) {
                            tweetText = text;
                        }
                    }
                }

                // Check content length (280 is X's standard tweet limit)
                const contentLength = tweetText.length;
                const isLongContent = contentLength > 280;

                // Determine post type
                // It's an article if:
                // 1. Has article title from page title, OR
                // 2. Has rich text view (twitterArticleRichTextView), OR
                // 3. Has article structure AND long content, OR
                // 4. Has "Article" label in page, OR
                // 5. Has "Show more" button (indicates long-form content)
                let hasShowMore = !!document.querySelector(
                    '[data-testid="tweet-text-show-more-link"]'
                );
                if (!hasShowMore) {
                    const buttons = document.querySelectorAll('div[role="button"]');
                    for (const btn of buttons) {
                        const text = btn.textContent.trim();
                        if (text === 'Show more' || text === '显示更多') {
                            hasShowMore = true;
                            break;
                        }
                    }
                }

                let postType = 'regular';
                const isArticle = articleTitle ||
                    hasRichTextView ||
                    (hasArticleStructure && isLongContent) ||
                    hasArticleLabel ||
                    (hasShowMore && isLongContent);
                if (isArticle) {
                    postType = 'article';
                }

                return {
                    post_type: postType,
                    article_title: articleTitle,
                    content_length: contentLength,
                    has_article_structure: hasArticleStructure,
                    has_article_label: hasArticleLabel,
                    has_show_more: hasShowMore,
                    has_rich_text_view: hasRichTextView
                };
            }
        """)

        logger.info(
            f"Detected post type: {result.get('post_type')} "
            f"(content length: {result.get('content_length')}, "
            f"has title: {bool(result.get('article_title'))}, "
            f"has rich text view: {result.get('has_rich_text_view')})"
        )

        return result

    async def detect_thread(self, page) -> Dict[str, Any]:
        """
        检测是否为连续贴(thread)并提取所有相关帖子

        X/Twitter 连续贴的特点是：
        1. 主帖显示为第一个 article 元素
        2. 发帖人自己的回复会显示在线程中
        3. 每个连续贴回复的第一个回复者就是发帖人自己
        4. 最多可以级联二十级

        Args:
            page: Playwright page object

        Returns:
            Dict 包含:
            - is_thread: bool - 是否为连续贴
            - main_author: str - 主帖作者
            - total_posts: int - 页面上所有帖子数量
            - thread_posts_count: int - 识别为连续贴的帖子数量
            - other_replies_count: int - 其他回复数量
            - thread_posts: List[Dict] - 连续贴列表（发帖人自己的回复）
            - other_replies: List[Dict] - 其他用户的回复列表
        """
        result = await page.evaluate("""
            () => {
                // 首先从URL中提取期望的作者
                const urlAuthorMatch = window.location.pathname.match(
                    /\\/([\\w_]+)\\/status\\//
                );
                const expectedAuthor = urlAuthorMatch ? '@' + urlAuthorMatch[1] : '';
                
                // 获取所有帖子元素 - 尝试多种选择器
                let posts = document.querySelectorAll('article[data-testid="tweet"]');
                
                // 如果没有找到，尝试其他选择器
                if (posts.length === 0) {
                    posts = document.querySelectorAll('article');
                }
                
                // 过滤掉没有内容的 article（可能是广告或其他元素）
                const validPosts = Array.from(posts).filter(post => {
                    const text = post.textContent.trim();
                    return text.length > 10; // 至少有一些文本内容
                });
                
                if (validPosts.length === 0) {
                    return {
                        is_thread: false,
                        main_author: expectedAuthor,
                        total_posts: 0,
                        thread_posts_count: 0,
                        other_replies_count: 0,
                        thread_posts: [],
                        other_replies: []
                    };
                }

                // 尝试多种选择器获取作者
                const authorSelectors = [
                    'a[href^="/"] > div > span',
                    '[data-testid="User-Name"] a',
                    'a[role="link"] span',
                    'div[data-testid="User-Name"] a span'
                ];
                
                // 尝试找到与URL作者匹配的主帖
                let mainPost = validPosts[0];
                let mainAuthor = '';
                
                // 首先尝试在validPosts中找到与URL作者匹配的帖子
                if (expectedAuthor) {
                    for (const post of validPosts) {
                        for (const selector of authorSelectors) {
                            const authorEl = post.querySelector(selector);
                            if (authorEl) {
                                const text = authorEl.textContent.trim();
                                if (text && text.toLowerCase() === expectedAuthor.toLowerCase()) {
                                    mainPost = post;
                                    mainAuthor = text;
                                    break;
                                }
                            }
                        }
                        if (mainAuthor) break;
                    }
                }
                
                // 如果没找到匹配的，使用第一个帖子
                if (!mainAuthor) {
                    for (const selector of authorSelectors) {
                        const authorEl = mainPost.querySelector(selector);
                        if (authorEl) {
                            const text = authorEl.textContent.trim();
                            if (text && text.startsWith('@')) {
                                mainAuthor = text;
                                break;
                            }
                        }
                    }
                }
                
                // 如果还是没找到，使用URL中的作者
                if (!mainAuthor && expectedAuthor) {
                    mainAuthor = expectedAuthor;
                }

                // 使用过滤后的有效帖子
                posts = validPosts;
                
                // 分析所有回复
                const threadPosts = [];
                const otherReplies = [];

                // 从第二个元素开始检查（第一个是主帖）
                for (let i = 1; i < posts.length; i++) {
                    const post = posts[i];
                    let postAuthor = '';

                    // 尝试获取回复作者
                    for (const selector of authorSelectors) {
                        const authorEl = post.querySelector(selector);
                        if (authorEl) {
                            const text = authorEl.textContent.trim();
                            if (text && text.startsWith('@')) {
                                postAuthor = text;
                                break;
                            }
                        }
                    }

                    // 提取回复内容
                    let content = '';
                    const contentSelectors = [
                        '[data-testid="tweetText"]',
                        'div[lang]',
                        '[role="link"] div[dir="auto"]'
                    ];

                    for (const selector of contentSelectors) {
                        const contentEl = post.querySelector(selector);
                        if (contentEl) {
                            content = contentEl.textContent.trim();
                            if (content) break;
                        }
                    }

                    // 提取时间戳
                    let timestamp = '';
                    const timeEl = post.querySelector('time');
                    if (timeEl) {
                        timestamp = timeEl.getAttribute('datetime') || timeEl.textContent.trim();
                    }

                    // 检查是否是发帖人自己的回复（连续贴）
                    // 条件：作者与主帖相同，或者是空作者（可能是加载问题）
                    const isOpReply = (postAuthor === mainAuthor) ||
                                      (postAuthor === '' && mainAuthor !== '');

                    const postInfo = {
                        index: i,
                        author: postAuthor || mainAuthor,
                        content: content,
                        timestamp: timestamp,
                        is_op_reply: isOpReply,
                        element_index: i
                    };

                    if (isOpReply) {
                        threadPosts.push(postInfo);
                    } else {
                        otherReplies.push(postInfo);
                    }
                }

                return {
                    is_thread: threadPosts.length > 0,
                    main_author: mainAuthor,
                    total_posts: posts.length,
                    thread_posts_count: threadPosts.length,
                    other_replies_count: otherReplies.length,
                    thread_posts: threadPosts,
                    other_replies: otherReplies
                };
            }
        """)

        logger.info(
            f"Thread detection: is_thread={result.get('is_thread')}, "
            f"main_author={result.get('main_author')}, "
            f"total_posts={result.get('total_posts')}, "
            f"thread_posts={result.get('thread_posts_count')}, "
            f"other_replies={result.get('other_replies_count')}"
        )

        return result

    async def detect_video(self, page) -> Dict[str, Any]:
        """
        Detect videos in the tweet page using JavaScript.

        Returns:
            Dict with:
            - has_video: bool - Whether any video is detected
            - video_urls: List[str] - List of native video URLs
            - embedded_videos: List[Dict] - List of embedded video info
                Each dict contains: type, url, title (if available)
        """
        result = await page.evaluate("""
            () => {
                const videoUrls = [];
                const embeddedVideos = [];

                // 1. Check for native X video player
                const videoPlayer = document.querySelector('[data-testid="videoPlayer"]');
                const hasNativeVideo = !!videoPlayer;

                // 2. Check for <video> tags
                const videoElements = document.querySelectorAll('video');
                videoElements.forEach(video => {
                    // Get video source
                    let src = video.getAttribute('src');
                    if (!src) {
                        const sourceEl = video.querySelector('source');
                        if (sourceEl) {
                            src = sourceEl.getAttribute('src');
                        }
                    }
                    if (src) {
                        // Skip blob URLs (browser-local, not accessible by yt-dlp)
                        if (src.startsWith('blob:')) {
                            return;
                        }
                        // Ensure absolute URL
                        if (src.startsWith('//')) src = 'https:' + src;
                        else if (src.startsWith('/')) src = window.location.origin + src;
                        if (!videoUrls.includes(src)) {
                            videoUrls.push(src);
                        }
                    }

                    // Note: poster is just a thumbnail image, not a video URL
                    // We don't add it to videoUrls to avoid confusion
                });

                // 3. Check for video in media containers
                const mediaContainers = document.querySelectorAll(
                    '[data-testid="tweetPhoto"], [data-testid="videoComponent"]'
                );
                mediaContainers.forEach(container => {
                    const videoEl = container.querySelector('video');
                    if (videoEl) {
                        let src = videoEl.getAttribute('src');
                        if (!src) {
                            const sourceEl = videoEl.querySelector('source');
                            if (sourceEl) {
                                src = sourceEl.getAttribute('src');
                            }
                        }
                        if (src) {
                            // Skip blob URLs (browser-local, not accessible by yt-dlp)
                            if (src.startsWith('blob:')) {
                                return;
                            }
                            if (src.startsWith('//')) src = 'https:' + src;
                            else if (src.startsWith('/')) src = window.location.origin + src;
                            if (!videoUrls.includes(src)) {
                                videoUrls.push(src);
                            }
                        }
                    }
                });

                // 4. Check for embedded iframes (YouTube, Vimeo, etc.)
                const iframes = document.querySelectorAll('iframe');
                iframes.forEach(iframe => {
                    const src = iframe.getAttribute('src');
                    if (src) {
                        // Detect video platform
                        let platform = null;
                        let videoId = null;

                        if (src.includes('youtube.com') || src.includes('youtu.be')) {
                            platform = 'youtube';
                            // Extract video ID
                            const match = src.match('/(?:v=\\\\/)([a-zA-Z0-9_-]{11})/');
                            if (match) videoId = match[1];
                        } else if (src.includes('vimeo.com')) {
                            platform = 'vimeo';
                            const match = src.match('/vimeo\\\\.com\\\\/(\\\\d+)/');
                            if (match) videoId = match[1];
                        } else if (src.includes('tiktok.com')) {
                            platform = 'tiktok';
                        } else if (src.includes('dailymotion.com')) {
                            platform = 'dailymotion';
                        } else if (src.includes('twitch.tv')) {
                            platform = 'twitch';
                        }

                        if (platform) {
                            embeddedVideos.push({
                                type: platform,
                                url: src,
                                videoId: videoId,
                                title: iframe.getAttribute('title') || null,
                                width: iframe.getAttribute('width') || null,
                                height: iframe.getAttribute('height') || null
                            });
                        }
                    }
                });

                // 5. Check for video cards/links
                const cardLinks = document.querySelectorAll(
                    'a[href*="youtube.com"], a[href*="youtu.be"], a[href*="vimeo.com"]'
                );
                cardLinks.forEach(link => {
                    const href = link.getAttribute('href');
                    if (href) {
                        let platform = null;
                        let videoId = null;

                        if (href.includes('youtube.com') || href.includes('youtu.be')) {
                            platform = 'youtube';
                            const match = href.match('/(?:v=\\\\/)([a-zA-Z0-9_-]{11})/');
                            if (match) videoId = match[1];
                        } else if (href.includes('vimeo.com')) {
                            platform = 'vimeo';
                            const match = href.match('/vimeo\\\\.com\\\\/(\\\\d+)/');
                            if (match) videoId = match[1];
                        }

                        if (platform) {
                            // Check if not already in embeddedVideos
                            const exists = embeddedVideos.some(v => v.url === href);
                            if (!exists) {
                                embeddedVideos.push({
                                    type: platform,
                                    url: href,
                                    videoId: videoId,
                                    title: link.textContent.trim() || null
                                });
                            }
                        }
                    }
                });

                return {
                    has_video: hasNativeVideo || videoUrls.length > 0 || embeddedVideos.length > 0,
                    video_urls: videoUrls,
                    embedded_videos: embeddedVideos
                };
            }
        """)

        logger.info(
            f"Video detection: has_video={result.get('has_video')}, "
            f"native_videos={len(result.get('video_urls', []))}, "
            f"embedded_videos={len(result.get('embedded_videos', []))}"
        )

        return result

    async def extract_external_links(self, page, base_url: str) -> List[Dict[str, str]]:
        """
        Extract external links from the tweet page.

        Filters out:
        - twitter.com, x.com domain links
        - Internal anchor links (starting with #)
        - javascript: links

        Args:
            page: Playwright page object
            base_url: Base URL for resolving relative links

        Returns:
            List of dicts with 'text' and 'url' keys
        """
        links = await page.evaluate("""
            (base) => {
                const links = [];
                const anchorElements = document.querySelectorAll('a');

                anchorElements.forEach(a => {
                    let href = a.getAttribute('href') || '';
                    const text = a.textContent.trim();

                    // Skip empty links
                    if (!href || !text) return;

                    // Resolve relative URLs
                    if (href.startsWith('/')) {
                        href = base + href;
                    } else if (href.startsWith('//')) {
                        href = 'https:' + href;
                    }

                    // Parse URL to check domain
                    let urlObj;
                    try {
                        urlObj = new URL(href);
                    } catch (e) {
                        return; // Invalid URL
                    }

                    // Filter out twitter/x domains
                    const hostname = urlObj.hostname.toLowerCase();
                    if (hostname.includes('twitter.com') ||
                        hostname.includes('x.com') ||
                        hostname.includes('mobile.twitter.com')) {
                        return;
                    }

                    // Filter out anchor-only links
                    if (href.startsWith('#')) return;

                    // Filter out javascript: links
                    if (href.toLowerCase().startsWith('javascript:')) return;

                    // Filter out analytics and intent links
                    if (href.includes('/analytics') ||
                        href.includes('/intent/') ||
                        href.includes('twitter.com/intent')) return;

                    // Filter out view counts and engagement metrics
                    if (text.match(/^\\d/) ||
                        text.match(/^[\\d,]+查看$/) ||
                        text.match(/^[\\d,]+ views$/i) ||
                        text.match(/^[\\d,]+$/) ||
                        text.length <= 1) return;

                    links.push({
                        text: text,
                        url: href
                    });
                });

                // Remove duplicates based on URL
                const seen = new Set();
                return links.filter(link => {
                    if (seen.has(link.url)) return false;
                    seen.add(link.url);
                    return true;
                });
            }
        """, base_url)

        logger.info(f"Extracted {len(links)} external links")
        return links

    async def extract_formatted_content(self, page, base_url: str) -> Dict[str, Any]:
        """Extract tweet content with formatting information and post type detection"""
        # First detect post type
        post_type_info = await self.detect_post_type(page)

        # Detect videos
        video_info = await self.detect_video(page)

        content_result = await page.evaluate("""
            (base) => {
                const tweetElement = document.querySelector('[data-testid="tweetText"]') ||
                                     document.querySelector('article [lang]') ||
                                     document.querySelector('article');

                if (!tweetElement) {
                    return {
                        text: '',
                        html: '',
                        formats: [],
                        images: [],
                        embeddedContent: [],
                        articleTitle: ''
                    };
                }

                // Extract article title if present
                let articleTitle = '';
                let pageTitleContent = '';  // 完整的页面标题内容（标题+正文）

                // 首先尝试从页面标题中提取（对于长文页面）
                // 英文格式: "(1) 作者 on X: "标题内容" / X"
                // 中文格式: "(1) X 上的 作者："标题内容" / X"
                // 注意：中文使用全角引号 ""，英文使用半角引号 ""
                const pageTitle = document.title;

                // 英文格式匹配 (on X: "...")
                if (pageTitle && pageTitle.includes(' on X:')) {
                    const titleMatch = pageTitle.match(/on X:\\s*"([^"]+)"/);
                    if (titleMatch && titleMatch[1]) {
                        pageTitleContent = titleMatch[1].trim();
                    }
                }

                // 中文格式匹配 (X 上的 作者："..." 或 X 上的 作者："...")
                if (!pageTitleContent && pageTitle && pageTitle.includes('X 上的')) {
                    const titleMatch = pageTitle.match(/X 上的[^：]+[：:]["\u201c]([^\u201d"]+)[\u201d"]/);
                    if (titleMatch && titleMatch[1]) {
                        pageTitleContent = titleMatch[1].trim();
                    }
                }

                // 通用格式匹配（尝试匹配引号中的内容，支持全角和半角引号）
                if (!pageTitleContent && pageTitle) {
                    const titleMatch = pageTitle.match(/[：:]["\u201c]([^\u201d"]+)[\u201d"]/);
                    if (titleMatch && titleMatch[1] && titleMatch[1].length > 5) {
                        pageTitleContent = titleMatch[1].trim();
                    }
                }

                if (pageTitleContent) {
                    // 从完整内容中提取标题部分
                    // 优先按句号/感叹号/问号分割，取第一句作为标题
                    const sentenceEnd = pageTitleContent.search(/[。！？]/);
                    if (sentenceEnd > 0) {
                        articleTitle = pageTitleContent.substring(0, sentenceEnd + 1);
                    } else {
                        // 没有句号分隔时，尝试按逗号分割（中文标题常用逗号）
                        const commaEnd = pageTitleContent.search(/[,，]/);
                        if (commaEnd > 0 && commaEnd <= 100) {
                            articleTitle = pageTitleContent.substring(0, commaEnd);
                        } else {
                            // 没有合适的标点分隔时，尝试按空格分割
                            const spaceIdx = pageTitleContent.indexOf(' ', 10);
                            if (spaceIdx > 10) {
                                articleTitle = pageTitleContent.substring(0, spaceIdx);
                            } else {
                                // 直接使用完整内容作为标题
                                articleTitle = pageTitleContent;
                            }
                        }
                    }
                }

                // 如果从页面标题没有提取到，尝试从页面元素中提取
                if (!articleTitle) {
                    const titleSelectors = [
                        '[data-testid="articleTitle"]',
                        '[data-testid="tweetTitle"]',
                        // 通用选择器
                        'article h2',
                        'article h1',
                        '[role="article"] h2',
                        '[role="article"] h1',
                        // 长文页面特定的选择器（放在通用之后，避免匹配到 "Article"/"Post" 等占位符）
                        'div[data-testid="cellInnerDiv"] h2',
                        'div[data-testid="cellInnerDiv"] h1',
                        // 第一个 article 中的大文本
                        'article:first-of-type div[dir="auto"]'
                    ];
                    for (const selector of titleSelectors) {
                        const el = document.querySelector(selector);
                        if (el) {
                            const text = el.textContent.trim();
                            // 过滤掉占位符文本（如 "Article", "Post", "Conversation" 等）
                            const placeholders = ['Article', 'Post', 'Conversation',
                                '文章', '帖子', '对话'];
                            if (text && text.length > 0 && text.length < 500
                                && !placeholders.includes(text)) {
                                articleTitle = text;
                                break;
                            }
                        }
                    }
                }

                const images = [];
                const imageMetadata = [];
                const imgElements = document.querySelectorAll(
                    '[data-testid="tweetPhoto"] img, img[src*="pbs.twimg.com/media"]'
                );
                imgElements.forEach(img => {
                    // Skip images inside video elements (thumbnails/posters)
                    if (img.closest('video')) {
                        return;
                    }

                    let src = img.getAttribute('src') || img.getAttribute('data-src');
                    if (src) {
                        if (src.startsWith('//')) src = 'https:' + src;
                        else if (src.startsWith('/')) src = base + src;
                        if (!images.includes(src)) {
                            images.push(src);
                            // Extract image metadata
                            const metadata = {
                                url: src,
                                width: img.naturalWidth || null,
                                height: img.naturalHeight || null,
                                alt: img.getAttribute('alt') || null
                            };
                            imageMetadata.push(metadata);
                        }
                    }
                });

                const codeBlocks = [];
                const embeddedContent = [];

                tweetElement.querySelectorAll('pre').forEach(pre => {
                    const code = pre.querySelector('code') || pre;
                    const text = code.textContent.trim();
                    if (text) {
                        let language = '';
                        const codeEl = pre.querySelector('code');
                        if (codeEl && codeEl.className) {
                            const langMatch = codeEl.className.match(/language-(\\w+)/);
                            if (langMatch) language = langMatch[1];
                        }

                        if (!language && text.startsWith('{') && text.endsWith('}')) {
                            language = 'json';
                        } else if (!language && text.startsWith('<') && text.endsWith('>')) {
                            language = 'html';
                        } else if (!language && (text.includes('function ') ||
                                    text.includes('const ') || text.includes('let '))) {
                            language = 'javascript';
                        } else if (!language && (text.includes('def ') ||
                                    text.includes('import '))) {
                            language = 'python';
                        } else if (!language && text.includes('typescript')) {
                            language = 'typescript';
                        } else if (!language && text.includes('markdown')) {
                            language = 'markdown';
                        }

                        embeddedContent.push({
                            type: 'code',
                            text: text,
                            language: language,
                            isMultiline: text.includes('\\n') || text.length > 50
                        });

                        codeBlocks.push({
                            text: text,
                            language: language,
                            isMultiline: true
                        });
                    }
                });

                tweetElement.querySelectorAll('code').forEach(c => {
                    if (!c.closest('pre')) {
                        const text = c.textContent.trim();
                        if (text && (text.includes('\\n') || text.length > 50)) {
                            let language = '';
                            if (c.className) {
                                const langMatch = c.className.match(/language-(\\w+)/);
                                if (langMatch) language = langMatch[1];
                            }
                            if (!language && text.startsWith('{') && text.endsWith('}')) {
                                language = 'json';
                            }
                            embeddedContent.push({
                                type: 'code',
                                text: text,
                                language: language,
                                isMultiline: true
                            });
                            codeBlocks.push({
                                text: text,
                                language: language,
                                isMultiline: true
                            });
                        }
                    }
                });

                const formats = [];

                tweetElement.querySelectorAll('a').forEach(a => {
                    let href = a.getAttribute('href');
                    const text = a.textContent.trim();
                    if (href && !href.includes('twitter.com/intent') && text) {
                        if (href.startsWith('/')) {
                            href = base + href;
                        }
                        if (!href.includes('/analytics') &&
                            !href.includes('/status/') &&
                            !text.match(/^\\d/) &&
                            !text.match(/^[\\d,]+查看$/) &&
                            !text.match(/^[\\d,]+ views$/i) &&
                            !text.match(/^[\\d,]+$/) &&
                            text.length > 1) {
                            formats.push({
                                type: 'link',
                                text: text,
                                href: href
                            });
                        }
                    }
                });

                tweetElement.querySelectorAll('strong, b').forEach(b => {
                    const text = b.textContent.trim();
                    if (text && text.length > 1) {
                        formats.push({ type: 'bold', text: text });
                    }
                });

                tweetElement.querySelectorAll('code').forEach(c => {
                    const text = c.textContent.trim();
                    if (text && !c.closest('pre')) {
                        const isMultiLine = text.includes('\\n') || text.length > 50;
                        if (!isMultiLine) {
                            formats.push({ type: 'code', text: text });
                        }
                    }
                });

                tweetElement.querySelectorAll('em, i').forEach(i => {
                    const text = i.textContent.trim();
                    if (text && text.length > 1 && !i.closest('a') && !i.closest('code')) {
                        formats.push({ type: 'italic', text: text });
                    }
                });

                let text = tweetElement.textContent.trim();
                text = text.replace(/[\\d,]+\\s*查看/g, '');
                text = text.replace(/[\\d,]+\\s*views/gi, '');
                text = text.replace(/想发布自己的文章？/g, '');
                text = text.replace(/升级为 Premium/g, '');
                text = text.replace(/[\\d,]+\\s*回复/g, '');
                text = text.replace(/[\\d,]+\\s*转帖/g, '');
                text = text.replace(/[\\d,]+\\s*喜欢/g, '');
                text = text.replace(/[\\d,]+\\s*书签/g, '');
                text = text.replace(/分享帖子/g, '');
                text = text.replace(/查看 \\d+ 条回复/g, '');
                text = text.replace(/[\\d,]+\\.?\\d*\\s*万/g, '');
                text = text.replace(/上午\\d+:\\d+\\s*·\\s*\\d+年\\d+月\\d+日/g, '');
                text = text.replace(/下午\\d+:\\d+\\s*·\\s*\\d+年\\d+月\\d+日/g, '');
                text = text.replace(/\\d+:\\d+\\s*[AP]M\\s*·\\s*\\w+\\s*\\d+,\\s*\\d+/g, '');
                text = text.replace(/^[^\\u4e00-\\u9fa5a-zA-Z\\[!"]+/, '');
                text = text.replace(/[·•]\\s*$/g, '');
                text = text.replace(/\\s+/g, ' ').trim();

                let html = tweetElement.innerHTML;
                html = html.replace(/<svg[^>]*>.*?<\\/svg>/gi, '');
                html = html.replace(/<button[^>]*data-testid="reply"[^>]*>.*?<\\/button>/gi, '');
                html = html.replace(/<button[^>]*data-testid="retweet"[^>]*>.*?<\\/button>/gi, '');
                html = html.replace(/<button[^>]*data-testid="like"[^>]*>.*?<\\/button>/gi, '');
                html = html.replace(/<button[^>]*data-testid="bookmark"[^>]*>.*?<\\/button>/gi, '');
                html = html.replace(/<a[^>]*analytics[^>]*>.*?<\\/a>/gi, '');
                html = html.replace(/<a[^>]*premium_sign_up[^>]*>.*?<\\/a>/gi, '');

                const contentParts = [];
                const firstArticle = (
                    document.querySelector('article[data-testid="tweet"]')
                    || document.querySelector('article')
                );
                const scope = firstArticle || document;

                function cleanText(t) {
                    t = t.replace(/[\\d,]+\\s*查看/g, '');
                    t = t.replace(/[\\d,]+\\s*views/gi, '');
                    t = t.replace(/想发布自己的文章？/g, '');
                    t = t.replace(/升级为 Premium/g, '');
                    t = t.replace(/[\\d,]+\\s*回复/g, '');
                    t = t.replace(/[\\d,]+\\s*转帖/g, '');
                    t = t.replace(/[\\d,]+\\s*喜欢/g, '');
                    t = t.replace(/[\\d,]+\\s*书签/g, '');
                    t = t.replace(/分享帖子/g, '');
                    t = t.replace(/查看 \\d+ 条回复/g, '');
                    t = t.replace(/[\\d,]+\\.?\\d*\\s*万/g, '');
                    t = t.replace(/上午\\d+:\\d+\\s*·\\s*\\d+年\\d+月\\d+日/g, '');
                    t = t.replace(/下午\\d+:\\d+\\s*·\\s*\\d+年\\d+月\\d+日/g, '');
                    t = t.replace(/\\d+:\\d+\\s*[AP]M\\s*·\\s*\\w+\\s*\\d+,\\s*\\d+/g, '');
                    t = t.replace(/^[^\\u4e00-\\u9fa5a-zA-Z\\[!"]+/, '');
                    t = t.replace(/[·•]\\s*$/g, '');
                    t = t.replace(/\\s+/g, ' ').trim();
                    return t;
                }

                function cleanHtml(h) {
                    h = h.replace(/<svg[^>]*>.*?<\\/svg>/gi, '');
                    h = h.replace(/<button[^>]*data-testid="reply"[^>]*>.*?<\\/button>/gi, '');
                    h = h.replace(/<button[^>]*data-testid="retweet"[^>]*>.*?<\\/button>/gi, '');
                    h = h.replace(/<button[^>]*data-testid="like"[^>]*>.*?<\\/button>/gi, '');
                    h = h.replace(/<button[^>]*data-testid="bookmark"[^>]*>.*?<\\/button>/gi, '');
                    h = h.replace(/<a[^>]*analytics[^>]*>.*?<\\/a>/gi, '');
                    h = h.replace(/<a[^>]*premium_sign_up[^>]*>.*?<\\/a>/gi, '');
                    return h;
                }

                const richTextView = document.querySelector(
                    '[data-testid="twitterArticleRichTextView"]'
                );

                if (richTextView) {
                    const richFullText = cleanText(
                        richTextView.textContent.trim()
                    );
                    const richFullHtml = cleanHtml(
                        richTextView.innerHTML
                    );

                    const langDivs = richTextView.querySelectorAll('div[lang]');
                    langDivs.forEach(el => {
                        const lang = el.getAttribute('lang') || '';
                        const t = cleanText(el.textContent.trim());
                        const h = cleanHtml(el.innerHTML);
                        if (t) {
                            contentParts.push({
                                lang: lang, text: t, html: h
                            });
                        }
                    });

                    // 优先使用 richTextView 的完整文本
                    // div[lang] 在匿名模式下可能不包含完整内容
                    if (richFullText && richFullText.length > 0) {
                        text = richFullText;
                        html = richFullHtml;
                    } else if (contentParts.length > 0) {
                        const zhPart = contentParts.find(p =>
                            p.lang.startsWith('zh')
                            || /[\\u4e00-\\u9fff]/.test(p.text)
                        );
                        if (zhPart) {
                            text = zhPart.text;
                            html = zhPart.html;
                        } else {
                            text = contentParts[0].text;
                            html = contentParts[0].html;
                        }
                    }

                    // 如果 lang divs 有更长的中文内容，优先使用
                    if (contentParts.length > 0 && text) {
                        const zhPart = contentParts.find(p =>
                            p.lang.startsWith('zh')
                            || /[\\u4e00-\\u9fff]/.test(p.text)
                        );
                        if (zhPart && zhPart.text.length > text.length) {
                            text = zhPart.text;
                            html = zhPart.html;
                        }
                    }

                    // 如果从 richTextView 提取的内容主要是英文，但页面标题中有中文内容，
                    // 则使用 pageTitleContent 作为内容（它来自 document.title，包含中文原文）
                    if (pageTitleContent && /[\\u4e00-\\u9fff]/.test(pageTitleContent) && text) {
                        const zhChars = (text.match(/[\\u4e00-\\u9fff]/g) || []).length;
                        const totalChars = text.length || 1;
                        const zhRatio = zhChars / totalChars;
                        if (zhRatio < 0.2) {
                            // 使用 pageTitleContent，但去除已提取的标题部分
                            if (articleTitle && pageTitleContent.startsWith(articleTitle)) {
                                text = pageTitleContent.substring(articleTitle.length).trim();
                            } else {
                                text = pageTitleContent;
                            }
                        }
                    }
                } else {
                    const tweetTexts = scope.querySelectorAll(
                        '[data-testid="tweetText"]'
                    );
                    tweetTexts.forEach(el => {
                        const lang = el.getAttribute('lang') || '';
                        const t = cleanText(el.textContent.trim());
                        const h = cleanHtml(el.innerHTML);
                        if (t) {
                            contentParts.push({
                                lang: lang, text: t, html: h
                            });
                        }
                    });

                    const translations = scope.querySelectorAll(
                        '[data-testid="translation"]'
                    );
                    translations.forEach(el => {
                        const transText = el.querySelector(
                            '[data-testid="tweetText"]'
                        );
                        if (transText) {
                            const lang = (
                                transText.getAttribute('lang') || ''
                            );
                            const t = cleanText(
                                transText.textContent.trim()
                            );
                            const h = cleanHtml(transText.innerHTML);
                            if (t) {
                                contentParts.push({
                                    lang: lang, text: t, html: h
                                });
                            }
                        }
                    });

                    if (contentParts.length >= 1) {
                        const zhPart = contentParts.find(p =>
                            p.lang.startsWith('zh')
                            || /[\\u4e00-\\u9fff]/.test(p.text)
                        );
                        if (zhPart) {
                            text = zhPart.text;
                            html = zhPart.html;
                        }
                    }

                    // 如果提取到的内容主要是英文，但页面标题中有中文内容，
                    // 则从页面标题中提取中文原文（X 会自动翻译推文，但标题保留原文）
                    // 判断"主要是英文"：中文字符占比低于 20%
                    if (pageTitleContent && /[\\u4e00-\\u9fff]/.test(pageTitleContent)) {
                        const zhChars = (text.match(/[\\u4e00-\\u9fff]/g) || []).length;
                        const totalChars = text.length || 1;
                        const zhRatio = zhChars / totalChars;
                        if (zhRatio < 0.2) {
                            // 使用 pageTitleContent，但去除已提取的标题部分
                            if (articleTitle && pageTitleContent.startsWith(articleTitle)) {
                                text = pageTitleContent.substring(articleTitle.length).trim();
                            } else {
                                text = pageTitleContent;
                            }
                        }
                    }
                }

                // 如果内容中包含标题，去除标题及其前面的噪声
                if (articleTitle && text.includes(articleTitle)) {
                    const titleIdx = text.indexOf(articleTitle);
                    if (titleIdx >= 0 && titleIdx < 200) {
                        text = text.substring(
                            titleIdx + articleTitle.length
                        ).trim();
                    }
                }

                // 清理内容开头的数字噪声（如浏览量、点赞数等）
                text = text.replace(/^[\\d,.\\s]+/, '');

                return {
                    text: text,
                    html: html,
                    formats: formats,
                    codeBlocks: codeBlocks,
                    images: images,
                    embeddedContent: embeddedContent,
                    articleTitle: articleTitle,
                    contentParts: contentParts
                };
            }
        """, base_url)

        # Merge post type information with content result
        content_result['post_type'] = post_type_info.get('post_type', 'regular')
        art_title = content_result.get('articleTitle')
        post_type_title = post_type_info.get('article_title', '')
        content_result['article_title'] = art_title or post_type_title

        # Add video information
        content_result['has_video'] = video_info.get('has_video', False)
        content_result['video_urls'] = video_info.get('video_urls', [])
        content_result['embedded_videos'] = video_info.get('embedded_videos', [])

        # Add image-related fields
        images = content_result.get('images', [])
        content_result['has_images'] = len(images) > 0
        content_result['image_metadata'] = content_result.get('imageMetadata', [])

        # Calculate media count (images + native videos)
        video_count = len(video_info.get('video_urls', []))
        image_count = len(images)
        content_result['media_count'] = image_count + video_count

        # Extract external links
        external_links = await self.extract_external_links(page, base_url)
        content_result['external_links'] = external_links

        return content_result

    async def extract_all_posts_content(
        self,
        page,
        base_url: str,
        thread_info: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        提取页面中所有帖子的详细内容，包括连续贴

        Args:
            page: Playwright page object
            base_url: Base URL for resolving relative links
            thread_info: 线程检测信息，来自 detect_thread()

        Returns:
            Dict 包含所有帖子的详细内容
        """
        if not thread_info.get('is_thread'):
            return {
                'is_thread': False,
                'main_post': None,
                'thread_posts': [],
                'other_replies': []
            }

        result = await page.evaluate("""
            (info) => {
                const posts = document.querySelectorAll(
                    'article[data-testid="tweet"]'
                );
                const mainAuthor = info.main_author;

                // 提取单个帖子的完整内容
                function extractPostContent(post, index) {
                    const postData = {
                        index: index,
                        author: '',
                        author_name: '',
                        content: '',
                        html: '',
                        timestamp: '',
                        images: [],
                        is_op_reply: false
                    };

                    // 提取作者信息
                    const authorSelectors = [
                        '[data-testid="User-Name"] a',
                        'a[href^="/"] > div > span',
                        'div[data-testid="User-Name"] a span'
                    ];

                    for (const selector of authorSelectors) {
                        const authorEl = post.querySelector(selector);
                        if (authorEl) {
                            const text = authorEl.textContent.trim();
                            if (text && text.startsWith('@')) {
                                postData.author = text;
                                break;
                            }
                        }
                    }

                    // 提取显示名称
                    const nameSelectors = [
                        '[data-testid="User-Name"] a span span',
                        'div[data-testid="User-Name"] span:first-child'
                    ];
                    for (const selector of nameSelectors) {
                        const nameEl = post.querySelector(selector);
                        if (nameEl) {
                            const text = nameEl.textContent.trim();
                            if (text && !text.startsWith('@')) {
                                postData.author_name = text;
                                break;
                            }
                        }
                    }

                    // 提取内容
                    const allTweetTexts = post.querySelectorAll('[data-testid="tweetText"]');
                    const contentParts = [];
                    allTweetTexts.forEach(el => {
                        const lang = el.getAttribute('lang') || '';
                        const t = el.textContent.trim();
                        const h = el.innerHTML;
                        if (t) {
                            contentParts.push({ lang: lang, text: t, html: h });
                        }
                    });
                    const translations = post.querySelectorAll('[data-testid="translation"]');
                    translations.forEach(el => {
                        const transText = el.querySelector('[data-testid="tweetText"]');
                        if (transText) {
                            const lang = transText.getAttribute('lang') || '';
                            const t = transText.textContent.trim();
                            const h = transText.innerHTML;
                            if (t) {
                                contentParts.push({
                                    lang: lang, text: t, html: h
                                });
                            }
                        }
                    });

                    if (contentParts.length > 0) {
                        postData.content_parts = contentParts;
                        const zhPart = contentParts.find(p =>
                            p.lang.startsWith('zh')
                            || /[\\u4e00-\\u9fff]/.test(p.text)
                        );
                        const primaryPart = zhPart || contentParts[0];
                        postData.content = primaryPart.text;
                        postData.html = primaryPart.html;
                    } else {
                        const contentSelectors = [
                            'div[lang]',
                            '[role="link"] div[dir="auto"]'
                        ];
                        for (const selector of contentSelectors) {
                            const contentEl = post.querySelector(selector);
                            if (contentEl) {
                                postData.content = contentEl.textContent.trim();
                                postData.html = contentEl.innerHTML;
                                break;
                            }
                        }
                    }

                    // 提取时间戳
                    const timeEl = post.querySelector('time');
                    if (timeEl) {
                        postData.timestamp = timeEl.getAttribute('datetime')
                            || timeEl.textContent.trim();
                    }

                    // 提取图片
                    const imgElements = post.querySelectorAll(
                        '[data-testid="tweetPhoto"] img, '
                        + 'img[src*="pbs.twimg.com/media"]'
                    );
                    imgElements.forEach(img => {
                        let src = img.getAttribute('src')
                            || img.getAttribute('data-src');
                        if (src) {
                            if (src.startsWith('//')) src = 'https:' + src;
                            else if (src.startsWith('/')) {
                                src = 'https://x.com' + src;
                            }
                            if (!postData.images.includes(src)) {
                                postData.images.push(src);
                            }
                        }
                    });

                    // 判断是否为发帖人自己的回复
                    postData.is_op_reply = (postData.author === mainAuthor)
                        || (postData.author === '' && mainAuthor !== '');

                    // 清理内容中的噪音
                    postData.content = postData.content
                        .replace(/[\\d,]+\\s*查看/g, '')
                        .replace(/[\\d,]+\\s*views/gi, '')
                        .replace(/[\\d,]+\\s*回复/g, '')
                        .replace(/[\\d,]+\\s*转帖/g, '')
                        .replace(/[\\d,]+\\s*喜欢/g, '')
                        .replace(/[\\d,]+\\s*书签/g, '')
                        .replace(/分享帖子/g, '')
                        .replace(/查看 \\d+ 条回复/g, '')
                        .replace(/\\s+/g, ' ')
                        .trim();

                    if (postData.content_parts) {
                        postData.content_parts = postData.content_parts.map(p => ({
                            lang: p.lang,
                            text: p.text
                                .replace(/[\\d,]+\\s*查看/g, '')
                                .replace(/[\\d,]+\\s*views/gi, '')
                                .replace(/[\\d,]+\\s*回复/g, '')
                                .replace(/[\\d,]+\\s*转帖/g, '')
                                .replace(/[\\d,]+\\s*喜欢/g, '')
                                .replace(/[\\d,]+\\s*书签/g, '')
                                .replace(/分享帖子/g, '')
                                .replace(/查看 \\d+ 条回复/g, '')
                                .replace(/\\s+/g, ' ')
                                .trim(),
                            html: p.html
                        }));
                    }

                    return postData;
                }

                // 提取所有帖子
                const allPosts = [];
                posts.forEach((post, index) => {
                    allPosts.push(extractPostContent(post, index));
                });

                // 分离主帖、连续贴和其他回复
                const mainPost = allPosts.length > 0 ? allPosts[0] : null;
                const threadPosts = [];
                const otherReplies = [];

                for (let i = 1; i < allPosts.length; i++) {
                    const post = allPosts[i];
                    if (post.is_op_reply) {
                        threadPosts.push(post);
                    } else {
                        otherReplies.push(post);
                    }
                }

                return {
                    is_thread: threadPosts.length > 0,
                    main_post: mainPost,
                    thread_posts: threadPosts,
                    other_replies: otherReplies,
                    all_posts: allPosts
                };
            }
        """, thread_info)

        logger.info(
            f"Extracted all posts: main_post={result.get('main_post') is not None}, "
            f"thread_posts={len(result.get('thread_posts', []))}, "
            f"other_replies={len(result.get('other_replies', []))}"
        )

        return result

    def convert_to_markdown(self, content: Dict[str, Any]) -> str:
        """Convert extracted content to Markdown format"""
        markdown = content.get('text', '')

        # Handle code blocks first
        code_block_placeholders = []
        if content.get('codeBlocks'):
            for i, block in enumerate(content['codeBlocks']):
                placeholder = f"__CODE_BLOCK_{i}__"
                code_block_placeholders.append({
                    'placeholder': placeholder,
                    'codeBlock': f"\n```\n{block['text']}\n```\n"
                })

                full_text = block['text']
                markdown = self._replace_code_in_markdown(
                    markdown, full_text, placeholder
                )

        # Sort formats by text length (longest first) to avoid partial replacements
        sorted_formats = sorted(
            content.get('formats', []),
            key=lambda x: len(x.get('text', '')),
            reverse=True
        )

        # Apply formatting
        for format_item in sorted_formats:
            format_type = format_item.get('type')
            text = format_item.get('text', '')

            if not text or text not in markdown:
                continue

            if format_type == 'bold':
                markdown = markdown.replace(text, f"**{text}**")
            elif format_type == 'italic':
                markdown = markdown.replace(text, f"*{text}*")
            elif format_type == 'code':
                markdown = markdown.replace(text, f"`{text}`")

        # Apply links last
        for format_item in sorted_formats:
            if format_item.get('type') == 'link':
                text = format_item.get('text', '')
                href = format_item.get('href', '')
                if text and text in markdown:
                    markdown = markdown.replace(text, f"[{text}]({href})")

        # Restore code blocks
        for item in code_block_placeholders:
            markdown = markdown.replace(item['placeholder'], item['codeBlock'])

        return markdown

    def _replace_code_in_markdown(self, markdown: str, full_text: str, placeholder: str) -> str:
        """Replace code text in markdown with placeholder, handling partial matches"""
        if full_text in markdown:
            return markdown.replace(full_text, placeholder)
        else:
            first_line = full_text.split('\n')[0]
            if first_line in markdown:
                start_idx = markdown.index(first_line)
                end_idx = start_idx
                lines = full_text.split('\n')
                for line in lines:
                    line_idx = markdown.find(line, end_idx)
                    if line_idx != -1:
                        end_idx = line_idx + len(line)
                if end_idx > start_idx:
                    return markdown[:start_idx] + placeholder + markdown[end_idx:]
        return markdown

    async def scrape_tweet(self, url: str) -> Dict[str, Any]:
        """
        Scrape tweet content from URL

        Args:
            url: Twitter/X tweet URL

        Returns:
            Dict with extracted content, title, images, etc.
        """
        await self.initialize_browser()

        clean_url = self._clean_tweet_url(url)
        if clean_url != url:
            logger.info(f"Cleaned URL: {clean_url} (removed tracking params)")

        page = await self.context.new_page()

        try:
            # Set HTTP headers (简化headers，避免影响页面渲染)
            await page.set_extra_http_headers({
                'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            })

            logger.info(f"Accessing tweet: {clean_url}")

            # Navigate to page
            response = await page.goto(
                clean_url, wait_until='domcontentloaded', timeout=90000
            )

            initial_status = response.status

            if initial_status == 200:
                # Good response, wait for content
                await self._wait_for_content(page)
                login_status = await self._check_login_status(page)
                if login_status.get('is_logged_in') or login_status.get('has_content'):
                    logger.info("Page loaded successfully with cookies")
                else:
                    logger.warning(
                        "200 but no content, "
                        "retrying without cookies"
                    )
                    page = await self._retry_without_cookies(
                        page, clean_url
                    )
            elif initial_status == 403:
                # 403 with cookies likely means cookies are invalid
                # X returns 403 for invalid auth instead of anonymous access
                logger.warning(
                    "Page returned 403, "
                    "cookies may be invalid, retrying without cookies"
                )
                page = await self._retry_without_cookies(
                    page, clean_url
                )
            else:
                logger.warning(f"Page returned status {initial_status}")
                await self._wait_for_content(page)
                login_status = await self._check_login_status(page)
                if not login_status.get('is_logged_in') and not login_status.get('has_content'):
                    return {
                        'success': False,
                        'error': (
                            f"页面加载失败 (HTTP {initial_status})，"
                            "可能需要更新 cookies"
                        )
                    }

            # Expand long tweets
            logger.info("Checking for long tweet expansion...")
            await self.expand_long_tweet(page)
            await page.wait_for_timeout(1000)

            # 先检测帖子类型，article 类型不需要展开回复
            # （article 的 expand_thread_replies 会点击"查看引用"导致页面导航离开）
            pre_detect = await self.detect_post_type(page)
            is_article_type = pre_detect.get('post_type') == 'article'

            if not is_article_type:
                # 尝试展开回复（连续贴）- 在提取内容之前展开
                logger.info("Checking for thread replies...")
                await self.expand_thread_replies(page)
            else:
                logger.info(
                    "Skipping thread reply expansion for article type"
                )

            # 等待内容加载完成
            await page.wait_for_timeout(3000)

            # 检测是否为连续贴(thread)
            logger.info("Detecting thread...")
            thread_info = await self.detect_thread(page)

            # Get page title
            title = await page.title()

            # Extract formatted content
            logger.info("Extracting formatted content...")
            content = await self.extract_formatted_content(page, 'https://x.com')

            # Fallback content extraction if primary method fails
            if not content.get('text'):
                selectors = [
                    '[data-testid="tweetText"]',
                    'article div[lang]',
                    '[data-testid="tweet"] div[dir="auto"]',
                    'div[role="link"] div[dir="auto"]',
                    'div[data-testid="cellInnerDiv"] div[lang]'
                ]

                for selector in selectors:
                    try:
                        elements = await page.query_selector_all(selector)
                        for element in elements:
                            text = await element.text_content()
                            if text and len(text.strip()) > len(content.get('text', '')):
                                content['text'] = text.strip()

                        if len(content.get('text', '')) > 50:
                            break
                    except Exception:
                        continue

            # Extract author and timestamp
            author_time_info = await self.extract_author_and_time(page)
            author = author_time_info.get('author', '')
            timestamp = author_time_info.get('timestamp', '')

            # 如果是连续贴，提取所有帖子内容
            thread_content = None
            if thread_info.get('is_thread'):
                logger.info(
                    f"Thread detected with {thread_info.get('thread_posts_count')} posts"
                )
                thread_content = await self.extract_all_posts_content(
                    page, 'https://x.com', thread_info
                )

            # Build result with post type information
            result = {
                'success': True,
                'title': title or 'X Post',
                'content': content.get('text', 'No content extracted'),
                'html': content.get('html', ''),
                'formats': content.get('formats', []),
                'codeBlocks': content.get('codeBlocks', []),
                'images': content.get('images', []),
                'url': url,
                'post_type': content.get('post_type', 'regular'),
                'article_title': content.get('article_title', ''),
                'external_links': content.get('external_links', []),
                'has_video': content.get('has_video', False),
                'video_urls': content.get('video_urls', []),
                'embedded_videos': content.get('embedded_videos', []),
                'content_parts': content.get('contentParts', []),
                'author': author,
                'timestamp': timestamp,
                # 连续贴相关信息
                'is_thread': thread_info.get('is_thread', False),
                'thread_info': {
                    'is_thread': thread_info.get('is_thread', False),
                    'main_author': thread_info.get('main_author', ''),
                    'total_posts': thread_info.get('total_posts', 1),
                    'thread_posts_count': thread_info.get(
                        'thread_posts_count', 0
                    ),
                    'other_replies_count': thread_info.get(
                        'other_replies_count', 0
                    )
                },
                'thread_content': thread_content
            }

            # Log post type for debugging
            post_type = result['post_type']
            article_title = result['article_title']
            is_thread = result['is_thread']

            if is_thread:
                logger.info(
                    f"Successfully extracted thread content "
                    f"({len(result['content'])} chars main, "
                    f"{result['thread_info']['thread_posts_count']} thread posts, "
                    f"{len(result['images'])} images)"
                )
            elif post_type == 'article':
                logger.info(
                    f"Successfully extracted article content "
                    f"({len(result['content'])} chars, {len(result['images'])} images, "
                    f"title: {article_title or 'N/A'})"
                )
            else:
                logger.info(
                    f"Successfully extracted tweet content "
                    f"({len(result['content'])} chars, {len(result['images'])} images)"
                )

            await page.close()
            return result

        except Exception as e:
            logger.error(f"Failed to scrape tweet: {e}")
            await page.close()
            return {
                'success': False,
                'error': str(e),
                'url': url
            }


class TwitterHandler(PlatformHandler):
    """Twitter/X platform handler for downloading tweets and media"""

    def __init__(self):
        super().__init__("Twitter/X")
        self.supported_content_types = [
            ContentType.TEXT,      # Tweet text content
            ContentType.IMAGE,     # Images in tweets
            ContentType.VIDEO,     # Videos in tweets
        ]
        self.extractor = TwitterContentExtractor()
        self.storage_service = StorageService()

    def _parse_twitter_error(self, error_msg: str) -> str:
        error_lower = error_msg.lower()
        if '登录失败' in error_msg or '重新登录' in error_msg:
            return "ERROR_LOGIN_FAILED"
        if 'rate limit' in error_lower or '速率限制' in error_msg:
            return "ERROR_RATE_LIMITED"
        if 'not found' in error_lower or '不存在' in error_msg:
            return "ERROR_TWEET_NOT_FOUND"
        if 'suspended' in error_lower or '封禁' in error_msg:
            return "ERROR_ACCOUNT_SUSPENDED"
        if 'protected' in error_lower or '私密' in error_msg:
            return "ERROR_TWEET_PROTECTED"
        return "ERROR_TWITTER_UNKNOWN"

    def generate_telegram_preview(
        self,
        result: Dict[str, Any],
        is_processing: bool = True,
        processing_state: str = ""
    ) -> str:
        """
        生成友好的 Telegram 提示信息

        根据内容类型显示不同的提示：
        - 长文 (Article)
        - 帖子 (Post)
        - 帖子(含视频) (Post with Video)
        - 连续贴+序号 (Thread with count)

        Args:
            result: scrape_tweet 返回的结果字典
            is_processing: 是否显示"正在处理"状态
            processing_state: 增量更新时的处理状态（如 "正在保存..."），会替换原状态行

        Returns:
            格式化的 Telegram 消息文本
        """
        if not result.get('success'):
            error_msg = result.get('error', '')
            error_code = self._parse_twitter_error(error_msg)
            twitter_error_messages = {
                "ERROR_LOGIN_FAILED": "🔐 Twitter/X 登录失败，可能需要重新登录",
                "ERROR_RATE_LIMITED": "⏳ Twitter/X 请求过于频繁，请稍后重试",
                "ERROR_TWEET_NOT_FOUND": "❌ 推文不存在或已被删除",
                "ERROR_ACCOUNT_SUSPENDED": "🚫 账号已被封禁",
                "ERROR_TWEET_PROTECTED": "🔒 推文来自私密账号，无法访问",
            }
            return twitter_error_messages.get(
                error_code,
                "❌ 无法获取内容信息"
            )

        post_type = result.get('post_type', 'regular')
        is_thread = result.get('is_thread', False)
        author = result.get('author', '@unknown')
        content = result.get('content', '')
        timestamp = result.get('timestamp', '')
        has_video = result.get('has_video', False)
        video_count = len(result.get('video_urls', []))
        image_count = len(result.get('images', []))
        thread_info = result.get('thread_info', {})
        thread_count = thread_info.get('thread_posts_count', 0)

        if is_thread:
            type_emoji = "🧵"
            type_label = f"连续贴 ({thread_count}条)"
        elif post_type == 'article':
            type_emoji = "📄"
            type_label = "长文"
        elif has_video:
            type_emoji = "🎬"
            type_label = f"帖子(含{video_count}个视频)"
        elif image_count > 0:
            type_emoji = "🖼️"
            type_label = f"帖子(含{image_count}张图片)"
        else:
            type_emoji = "💬"
            type_label = "帖子"

        content_preview = content[:100] if len(content) > 100 else content
        if len(content) > 100:
            content_preview += "..."

        time_str = ""
        if timestamp:
            try:
                from datetime import datetime
                dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                time_str = dt.strftime('%Y-%m-%d %H:%M')
            except (ValueError, TypeError):
                time_str = timestamp[:16] if len(timestamp) > 16 else timestamp

        lines = [
            "🐦 Twitter/X 内容",
            "",
            f"{type_emoji} 类型: {type_label}",
            f"👤 作者: {author}",
        ]

        if post_type == 'article' and result.get('article_title'):
            lines.append(f"📰 标题: {result['article_title'][:80]}")

        if content_preview:
            lines.append(f"📝 预览: {content_preview}")

        if time_str:
            lines.append(f"⏰ 时间: {time_str}")

        stats = []
        if result.get('external_links'):
            stats.append(f"{len(result['external_links'])} 个链接")
        if stats:
            lines.append(f"📊 包含: {' | '.join(stats)}")

        if is_processing:
            lines.append("")
            if processing_state:
                lines.append(processing_state)
            else:
                lines.append("⏳ 正在获取内容，请稍候...")

        return "\n".join(lines)

    def build_processing_state(
        self,
        result: Dict[str, Any],
        state: str
    ) -> str:
        """
        构建处理状态行，用于增量更新消息底部的处理状态。

        Args:
            result: scrape_tweet 返回的结果字典
            state: 处理状态描述

        Returns:
            处理状态行文本
        """
        return f"⏳ {state}"

    async def fetch_link_preview(self, url: str) -> Dict[str, Optional[str]]:
        """
        Fetch link preview information (Open Graph tags) for a given URL.

        Args:
            url: The URL to fetch preview for

        Returns:
            Dict with 'title', 'description', and 'image' keys (values may be None)
        """
        if aiohttp is None:
            logger.warning("aiohttp not installed, cannot fetch link preview")
            return {'title': None, 'description': None, 'image': None}

        try:
            timeout = aiohttp.ClientTimeout(total=5)
            session = await self.get_http_session()
            async with session.get(url, timeout=timeout, headers={
                'User-Agent': (
                    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                    'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
                ),
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            }) as response:
                if response.status != 200:
                    logger.warning(f"Failed to fetch link preview: HTTP {response.status}")
                    return {'title': None, 'description': None, 'image': None}

                html = await response.text()

                # Parse Open Graph tags
                result = {'title': None, 'description': None, 'image': None}

                # og:title
                title_pat = (
                    r'<meta[^>]*property=["\']og:title["\'][^>]*'
                    r'content=["\']([^"\']+)["\']'
                )
                title_match = re.search(title_pat, html, re.IGNORECASE)
                if not title_match:
                    title_pat2 = (
                        r'<meta[^>]*content=["\']([^"\']+)["\'][^>]*'
                        r'property=["\']og:title["\']'
                    )
                    title_match = re.search(title_pat2, html, re.IGNORECASE)
                if title_match:
                    result['title'] = title_match.group(1).strip()

                # og:description
                desc_pat = (
                    r'<meta[^>]*property=["\']og:description["\'][^>]*'
                    r'content=["\']([^"\']+)["\']'
                )
                desc_match = re.search(desc_pat, html, re.IGNORECASE)
                if not desc_match:
                    desc_pat2 = (
                        r'<meta[^>]*content=["\']([^"\']+)["\'][^>]*'
                        r'property=["\']og:description["\']'
                    )
                    desc_match = re.search(desc_pat2, html, re.IGNORECASE)
                if desc_match:
                    result['description'] = desc_match.group(1).strip()

                # og:image
                img_pat = (
                    r'<meta[^>]*property=["\']og:image["\'][^>]*'
                    r'content=["\']([^"\']+)["\']'
                )
                image_match = re.search(img_pat, html, re.IGNORECASE)
                if not image_match:
                    img_pat2 = (
                        r'<meta[^>]*content=["\']([^"\']+)["\'][^>]*'
                        r'property=["\']og:image["\']'
                    )
                    image_match = re.search(img_pat2, html, re.IGNORECASE)
                if image_match:
                    result['image'] = image_match.group(1).strip()

                has_title = result['title'] is not None
                logger.info(f"Fetched link preview for {url}: title={has_title}")
                return result

        except asyncio.TimeoutError:
            logger.warning(f"Timeout fetching link preview for {url}")
            return {'title': None, 'description': None, 'image': None}
        except Exception as e:
            logger.warning(f"Error fetching link preview for {url}: {e}")
            return {'title': None, 'description': None, 'image': None}

    def generate_title(
        self,
        post_type: str,
        content: str,
        author: str,
        external_links: List[Dict[str, Any]],
        has_video: bool,
        article_title: str = ""
    ) -> str:
        """
        Generate a smart title based on post type and content.

        Args:
            post_type: 'article' or 'regular'
            content: The post content text
            author: The post author
            external_links: List of external links in the post
            has_video: Whether the post contains video
            article_title: The article title (for article type)

        Returns:
            Generated title string
        """
        # 清理 article_title，去除常见的占位符
        cleaned_article_title = article_title.strip() if article_title else ""

        # 如果 article_title 是常见的占位符，则忽略它
        placeholder_titles = ['Article', 'Post', "Today's News", '']
        if cleaned_article_title in placeholder_titles:
            cleaned_article_title = ""

        if post_type == 'article' and cleaned_article_title:
            return self.clean_title(cleaned_article_title)

        # For article type without explicit title, extract from content
        if post_type == 'article' and content and content.strip():
            # 尝试从内容中提取标题
            # 内容格式可能是: "标题，副标题 正文内容" 或 "标题。正文内容"
            content_text = content.strip()
            # 先按句号分割
            delimiters = r'[。！？]'
            sentences = re.split(delimiters, content_text, maxsplit=1)
            first_sentence = sentences[0].strip() if sentences else ""

            if first_sentence and len(first_sentence) <= 100:
                title = first_sentence
            elif first_sentence and len(first_sentence) > 100:
                # 第一句太长，尝试按逗号分割
                comma_parts = re.split(r'[,，]', first_sentence, maxsplit=1)
                if len(comma_parts) > 1 and 4 <= len(comma_parts[0]) <= 100:
                    title = comma_parts[0]
                else:
                    # 尝试按空格分割（标题和正文之间通常有空格）
                    parts = content_text.split(None, 1)
                    if len(parts) > 1 and 4 <= len(parts[0]) <= 100:
                        title = parts[0]
                    else:
                        title = content_text[:80]
            else:
                title = content_text[:80]

            # Add markers
            markers = []
            if has_video:
                markers.append("[含视频]")
            if external_links:
                markers.append("[含链接]")

            if markers:
                title = f"{' '.join(markers)} {title}"

            return self.clean_title(title)

        # For regular posts
        title = ""

        if content and content.strip():
            # Extract first sentence (split by 。!?)
            delimiters = r'[。！？\.\!?]'
            sentences = re.split(delimiters, content.strip())
            first_sentence = sentences[0].strip() if sentences else ""

            if first_sentence:
                # Limit to 80 characters
                if len(first_sentence) > 80:
                    title = first_sentence[:80]
                else:
                    title = first_sentence
            else:
                # If no sentence delimiter found, take first 80 chars
                title = content.strip()[:80]
        else:
            # Empty content, use {@author} - {date} format
            date_str = datetime.now().strftime('%Y-%m-%d')
            title = f"{author} - {date_str}"

        # Add markers
        markers = []
        if has_video:
            markers.append("[含视频]")
        if external_links:
            markers.append("[含链接]")

        if markers:
            title = f"{' '.join(markers)} {title}"

        return self.clean_title(title)

    def clean_title(self, title: str) -> str:
        """
        Clean and normalize the title.

        - Remove newlines and extra spaces
        - Keep Chinese/English characters, numbers, common punctuation
        - Remove emoji and special symbols
        - Max 100 characters, add ellipsis if truncated

        Args:
            title: Raw title string

        Returns:
            Cleaned title string
        """
        if not title:
            return ""

        # Remove newlines and normalize spaces
        cleaned = title.replace('\n', ' ').replace('\r', ' ')
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()

        # Keep only allowed characters:
        # - Chinese characters (\u4e00-\u9fff)
        # - English letters (a-zA-Z)
        # - Numbers (0-9)
        # - Common punctuation: ，。！？、：""''…—
        # - Technical/financial symbols: +-%$@#/
        # - Spaces
        # - Square brackets for markers: [ ]
        allowed_pattern = (
            r'[^\u4e00-\u9fff\u3000-\u303fa-zA-Z0-9\s'
            r'，。！？、：""''…—'
            r'+\-％%$@#/'
            r'\[\]]'
        )
        cleaned = re.sub(allowed_pattern, '', cleaned)

        # Normalize spaces again
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()

        # Truncate to 100 characters and add ellipsis if needed
        max_length = 100
        if len(cleaned) > max_length:
            cleaned = cleaned[:max_length - 1] + "…"

        return cleaned

    def can_handle(self, url: str) -> bool:
        """Check if this handler can process the given URL"""
        if not self.validate_url(url):
            return False

        # Twitter/X URL patterns
        twitter_patterns = [
            'twitter.com/',
            'x.com/',
            'mobile.twitter.com/'
        ]

        url_lower = url.strip().lower()
        return any(pattern in url_lower for pattern in twitter_patterns)

    def is_tweet_url(self, url: str) -> bool:
        """Check if URL is a specific tweet"""
        # Pattern: twitter.com/username/status/1234567890
        tweet_pattern = r'(twitter|x)\.com/\w+/status/\d+'
        return re.search(tweet_pattern, url.strip()) is not None

    def extract_tweet_id(self, url: str) -> Optional[str]:
        """Extract tweet ID from URL"""
        if not self.is_tweet_url(url):
            return None

        # Extract ID from status/1234567890
        match = re.search(r'status/(\d+)', url)
        return match.group(1) if match else None

    async def get_content_info(self, url: str) -> Optional[ContentInfo]:
        """Get information about the tweet without downloading"""
        try:
            tweet_id = self.extract_tweet_id(url)
            if not tweet_id:
                logger.warning(f"Could not extract tweet ID from URL: {url}")
                return None

            logger.info(f"Getting tweet info for ID: {tweet_id}")

            # Scrape tweet content
            result = await self.extractor.scrape_tweet(url)

            if not result.get('success'):
                error_msg = result.get('error', 'Unknown error')
                logger.error(f"Failed to scrape tweet: {error_msg}")
                return ContentInfo(
                    url=url,
                    title='Unknown',
                    error_detail=self._parse_twitter_error(error_msg)
                )

            # Determine content type based on media
            content_type = ContentType.TEXT
            html_content = result.get('html', '')
            # Use has_video from scrape result (detected by detect_video method)
            has_video = result.get('has_video', False)
            if result.get('images'):
                content_type = ContentType.IMAGE
            # Check for video - use has_video from result or check HTML
            video_in_html = (html_content and
                             ('<video' in html_content or
                              ">video</span>" in html_content or
                              'data-testid="videoPlayer"' in html_content))
            if has_video or video_in_html:
                content_type = ContentType.VIDEO
                has_video = True

            # Extract author from URL or title
            author_match = re.search(r'(twitter|x)\.com/(\w+)/status/', url)
            author = f"@{author_match.group(2)}" if author_match else "Unknown"

            # Get post type and article title from result
            post_type = result.get('post_type', 'regular')
            article_title = result.get('article_title', '')
            content = result.get('content', '')
            formats = result.get('formats', [])

            # Extract external links from formats
            external_links = [
                fmt for fmt in formats
                if fmt.get('type') == 'link'
            ]

            # Generate smart title
            generated_title = self.generate_title(
                post_type=post_type,
                content=content,
                author=author,
                external_links=external_links,
                has_video=has_video,
                article_title=article_title
            )

            # Get thread info from result
            is_thread = result.get('is_thread', False)
            thread_info = result.get('thread_info', {})

            return ContentInfo(
                url=url,
                title=generated_title,
                description=content[:200],
                content_type=content_type,
                uploader=author,
                upload_date=datetime.now().strftime('%Y-%m-%d'),
                metadata={
                    'tweet_id': tweet_id,
                    'images': result.get('images', []),
                    'formats': formats,
                    'full_content': content,
                    'html': html_content,
                    'post_type': post_type,
                    'article_title': article_title,
                    'has_video': has_video,
                    'external_links': external_links,
                    'is_thread': is_thread,
                    'thread_info': thread_info
                }
            )

        except Exception as e:
            logger.error(f"Failed to get Twitter content info for {url}: {e}")
            return None

    async def download_content(
        self,
        url: str,
        content_type: ContentType,
        progress_callback=None,
        format_id: str | None = None,
        pre_scraped_result: Optional[Dict[str, Any]] = None
    ) -> DownloadResult:
        """Download content from Twitter/X"""
        try:
            logger.info(
                "Downloading Twitter content: %s (%s)",
                url,
                content_type.value
            )

            if progress_callback:
                await progress_callback({"status": "fetching", "progress": 20})

            # Reuse pre-scraped result if available to avoid redundant
            # browser access
            if pre_scraped_result and pre_scraped_result.get('success'):
                result = pre_scraped_result
                logger.info(
                    "♻️ Reusing pre-scraped result (skipped redundant scrape)"
                )
            else:
                result = await self.extractor.scrape_tweet(url)

            if not result.get('success'):
                err_msg = result.get('error', 'Failed to scrape tweet')
                return DownloadResult(
                    success=False,
                    error_message=err_msg
                )

            if progress_callback:
                await progress_callback({"status": "processing", "progress": 50})

            temp_dir = tempfile.mkdtemp()
            images_dir = os.path.join(temp_dir, 'images')
            os.makedirs(images_dir, exist_ok=True)

            # Download images
            images = result.get('images', [])
            local_images = {}
            if images:
                if progress_callback:
                    await progress_callback({
                        "status": "downloading_images",
                        "progress": 60
                    })
                local_images = await self._download_images(images, images_dir)

            # Download videos if present
            video_urls = result.get('video_urls', [])
            local_videos = []
            has_video = result.get('has_video', False)
            # Download videos if detected (regardless of content_type or post_type)
            # yt-dlp can extract videos from tweet URLs even for article-type posts
            if has_video:
                if progress_callback:
                    await progress_callback({
                        "status": "downloading_videos",
                        "progress": 70
                    })
                videos_dir = os.path.join(temp_dir, 'videos')
                os.makedirs(videos_dir, exist_ok=True)

                # Filter out blob URLs (browser-local, not accessible by yt-dlp)
                # and Twitter/X article URLs (not supported by yt-dlp)
                valid_video_urls = [
                    url for url in video_urls
                    if url and not url.startswith('blob:') and '/i/article/' not in url
                ]

                # If we have valid video URLs, use them
                # Otherwise, use the tweet URL itself for yt-dlp to extract
                if valid_video_urls:
                    for video_url in valid_video_urls:
                        video_path = await self.download_video(
                            video_url, videos_dir
                        )
                        if video_path:
                            local_videos.append(video_path)
                            logger.info(f"Video saved to: {video_path}")
                elif '/i/article/' not in url:
                    # Use the original tweet URL to download video
                    logger.info(
                        f"No valid video URLs, using tweet URL: {url}"
                    )
                    video_path = await self.download_video(url, videos_dir)
                    if video_path:
                        local_videos.append(video_path)
                        logger.info(f"Video saved to: {video_path}")

                # Log videos directory contents
                if os.path.exists(videos_dir):
                    video_files = os.listdir(videos_dir)
                    logger.info(f"Videos in {videos_dir}: {video_files}")

            tweet_id = self.extract_tweet_id(url) or 'unknown'
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f"tweet_{tweet_id}_{timestamp}.html"
            temp_file = os.path.join(temp_dir, filename)

            html_content = self._generate_html(result, local_images, local_videos)
            with open(temp_file, 'w', encoding='utf-8') as f:
                f.write(html_content)

            logger.info(f"Saved tweet content to temporary file: {temp_file}")

            # Generate PDF alongside HTML
            pdf_file = None
            if pdf_converter.is_available():
                try:
                    pdf_filename = filename.replace('.html', '.pdf')
                    pdf_file = os.path.join(temp_dir, pdf_filename)

                    video_thumbnails = (
                        self._generate_video_thumbnails_mapping(local_videos)
                    )

                    pdf_result = await pdf_converter.convert_html_to_pdf(
                        temp_file,
                        pdf_file,
                        preprocess=True,
                        video_thumbnails=video_thumbnails
                    )

                    if pdf_result and os.path.exists(pdf_result):
                        file_size = os.path.getsize(pdf_result)
                        logger.info(
                            f"PDF generated: {pdf_result} ({file_size} bytes)"
                        )
                    else:
                        logger.warning("PDF generation returned empty result")
                        pdf_file = None
                except Exception as e:
                    logger.error(f"PDF generation failed: {e}")
                    pdf_file = None
            else:
                logger.warning("PDF converter not available, skipping PDF generation")

            if progress_callback:
                await progress_callback({"status": "completed", "progress": 100})

            # Extract author
            author = result.get('author', '')
            if not author:
                author_match = re.search(r'(twitter|x)\.com/(\w+)/status/', url)
                author = f"@{author_match.group(2)}" if author_match else "Unknown"

            # Get post type and generate title
            post_type = result.get('post_type', 'regular')
            article_title = result.get('article_title', '')
            content_text = result.get('content', '')
            has_video = result.get('has_video', False)
            external_links = result.get('external_links', [])

            # Generate smart title
            generated_title = self.generate_title(
                post_type=post_type,
                content=content_text,
                author=author,
                external_links=external_links,
                has_video=has_video,
                article_title=article_title
            )

            # Determine content type based on media
            actual_content_type = ContentType.TEXT
            if result.get('has_video') or local_videos:
                actual_content_type = ContentType.VIDEO
            elif result.get('images') or local_images:
                actual_content_type = ContentType.IMAGE

            # Build complete metadata
            metadata = {
                'tweet_id': tweet_id,
                'images': list(local_images.values()),
                'videos': local_videos,
                'formats': result.get('formats', []),
                'full_content': content_text,
                'html': result.get('html', ''),
                'post_type': post_type,
                'article_title': article_title,
                'has_video': has_video,
                'video_urls': video_urls,
                'embedded_videos': result.get('embedded_videos', []),
                'external_links': external_links,
                'author': author,
                'timestamp': result.get('timestamp', ''),
                'local_images': local_images,
                'local_videos': local_videos
            }

            content_info = ContentInfo(
                url=url,
                title=generated_title,
                description=content_text[:200],
                content_type=actual_content_type,
                uploader=author,
                upload_date=datetime.now().strftime('%Y-%m-%d'),
                metadata=metadata
            )

            return DownloadResult(
                success=True,
                file_path=temp_dir,
                content_info=content_info,
                error_message=None,
                temp_dir=temp_dir
            )

        except Exception as e:
            logger.error(f"Failed to download Twitter content: {e}")
            # Clean up temp_dir on failure
            temp_dir = locals().get('temp_dir')
            if temp_dir and os.path.exists(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)
            return DownloadResult(
                success=False,
                error_message=str(e)
            )

    def _generate_video_thumbnails_mapping(
        self,
        local_videos: List[str]
    ) -> Dict[str, str]:
        """Generate thumbnail paths for videos"""
        from pathlib import Path as PathLib

        thumbnails = {}

        for video_path in local_videos:
            if not video_path or not os.path.exists(video_path):
                continue

            video_path_obj = PathLib(video_path)

            possible_thumbnails = [
                video_path_obj.with_suffix('.jpg'),
                video_path_obj.with_suffix('.png'),
                video_path_obj.with_suffix('.thumb.jpg'),
                video_path_obj.with_suffix('.thumb.png'),
                video_path_obj.parent / 'thumbnails' / f'{video_path_obj.stem}.jpg',
                video_path_obj.parent / 'thumbnails' / f'{video_path_obj.stem}.png',
            ]

            for thumb in possible_thumbnails:
                if thumb.exists():
                    thumbnails[video_path] = str(thumb)
                    break

        return thumbnails

    def _calculate_reading_time(self, text: str) -> int:
        """Calculate estimated reading time in minutes.

        Chinese: ~400 chars/min, English: ~200 words/min.
        Returns at least 1 minute.
        """
        if not text:
            return 1
        zh_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
        non_zh_text = ''.join(
            c for c in text if not ('\u4e00' <= c <= '\u9fff')
        )
        en_words = len(non_zh_text.split()) if non_zh_text.strip() else 0
        minutes = zh_chars / 400 + en_words / 200
        return max(1, int(minutes + 0.5))

    def _add_heading_ids(self, html: str) -> tuple:
        """Add id attributes to h2/h3 tags and return (modified_html, headings_list).

        headings_list: [{'id': 'toc-1', 'level': 2, 'text': '...'}, ...]
        """
        headings = []
        h2_counter = 0
        h3_counter = 0

        def replace_heading(match):
            nonlocal h2_counter, h3_counter
            tag = match.group(1)  # h2 or h3
            attrs = match.group(2) or ''  # existing attributes
            text = match.group(3)  # heading text

            if tag == 'h2':
                h2_counter += 1
                h3_counter = 0
                heading_id = f'toc-{h2_counter}'
            else:
                h3_counter += 1
                heading_id = f'toc-{h2_counter}-{h3_counter}'

            headings.append({
                'id': heading_id,
                'level': int(tag[1]),
                'text': text.strip()
            })

            # Preserve existing attributes but add id
            if 'id=' in attrs:
                return match.group(0)
            return f'<{tag}{attrs} id="{heading_id}">{text}</{tag}>'

        modified = re.sub(
            r'<(h[23])(\s[^>]*)?>(.*?)</\1>',
            replace_heading,
            html,
            flags=re.DOTALL
        )
        return modified, headings

    def _generate_toc(self, headings: list) -> str:
        """Generate TOC HTML from headings list.

        Only generates TOC if there are >= 2 headings.
        Returns empty string otherwise.
        """
        if len(headings) < 2:
            return ''

        toc_items = []
        for h in headings:
            indent = '    ' if h['level'] == 3 else '  '
            font_size = '0.95em' if h['level'] == 3 else '1em'
            toc_items.append(
                f'{indent}<li class="toc-level-{h["level"]}">'
                f'<a href="#{h["id"]}" style="font-size:{font_size}">'
                f'{h["text"]}</a></li>'
            )

        items_html = '\n'.join(toc_items)
        return f'''    <div class="toc">
        <h2>目录</h2>
        <ul>
{items_html}
        </ul>
    </div>
'''

    def _generate_html(
        self,
        result: Dict[str, Any],
        local_images: Dict[str, Any] = None,
        local_videos: List[str] = None
    ) -> str:
        """
        Generate HTML file with unified template.

        Template structure:
        - Header: different styles for article vs regular posts
        - Body: content with preserved formatting
        - Media section: images with local paths, video players
        - External links section: clickable cards
        """
        # Get basic info
        post_type = result.get('post_type', 'regular')
        article_title = result.get('article_title', '')
        url = result.get('url', '')
        content = result.get('content', '')
        html_content = result.get('html', '')
        local_images = local_images or {}
        local_videos = local_videos or []

        # Determine title based on post type
        if post_type == 'article' and article_title:
            display_title = article_title
        else:
            display_title = result.get('title', 'X Post')

        # Get author and publish time
        author = result.get('author', '')
        if not author:
            author_match = re.search(
                r'(twitter|x)\.com/(\w+)/status/', url
            )
            author = f"@{author_match.group(2)}" if author_match else "@unknown"

        publish_time = result.get('publish_time', '')
        if not publish_time:
            publish_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        # Get media info
        images = result.get('images', [])
        video_urls = result.get('video_urls', [])
        embedded_videos = result.get('embedded_videos', [])
        external_links = result.get('external_links', [])

        # Determine if article (needed before header and reading time)
        is_article = (post_type == 'article')

        # Calculate reading time for articles
        reading_time = ''
        if is_article:
            rt = self._calculate_reading_time(content)
            reading_time = f'<p class="reading-time">阅读时间: 约{rt}分钟</p>'

        # Build header HTML based on post type
        if post_type == 'article' and article_title:
            header_html = f'''
    <div class="header article-header">
        <span class="post-type-badge article">📄 Article</span>
        <h1>{display_title}</h1>
        <div class="meta">
            <p class="author">作者: {author}</p>
            <p class="time">发布时间: {publish_time}</p>
            {reading_time}
            <p class="source">原文: <a href="{url}" target="_blank">{url}</a></p>
        </div>
    </div>'''
        else:
            header_html = f'''
    <div class="header regular-header">
        <span class="post-type-badge regular">Post</span>
        <h1>{display_title}</h1>
        <div class="meta">
            <p class="author">作者: {author}</p>
            <p class="time">发布时间: {publish_time}</p>
            <p class="source">原文: <a href="{url}" target="_blank">{url}</a></p>
        </div>
    </div>'''

        # Build content HTML
        content_parts = result.get('content_parts', [])

        if is_article and html_content:
            clean_content = self._clean_html_content(
                html_content, local_images
            )
        elif content_parts and len(content_parts) > 1:
            clean_content = ''
            for part in content_parts:
                lang = part.get('lang', '')
                part_text = part.get('text', '')
                is_zh = (lang.startswith('zh')
                         or any('\u4e00' <= c <= '\u9fff'
                                for c in part_text))
                lang_label = '中文' if is_zh else 'English'
                lang_class = 'bilingual-zh' if is_zh else 'bilingual-en'
                part_html = part.get('html', '')
                if part_html:
                    clean_part = self._clean_html_content(
                        part_html, local_images
                    )
                else:
                    clean_part = f'<p>{part_text}</p>'
                clean_content += (
                    f'<div class="bilingual-block {lang_class}">'
                    f'<span class="lang-label">{lang_label}</span>'
                    f'{clean_part}</div>\n'
                )
        elif content_parts and len(content_parts) == 1:
            part = content_parts[0]
            part_html = part.get('html', '')
            if part_html:
                clean_content = self._clean_html_content(
                    part_html, local_images
                )
            else:
                clean_content = f'<p>{part.get("text", content)}</p>'
        elif html_content:
            clean_content = self._clean_html_content(html_content, local_images)
        else:
            clean_content = f'<p>{content}</p>'

        # Generate TOC and add heading ids for articles
        toc_html = ''
        if is_article and clean_content:
            clean_content, headings = self._add_heading_ids(clean_content)
            toc_html = self._generate_toc(headings)
            # Add .lead class to first <p> for articles
            clean_content = re.sub(
                r'<p(?=\s|>)',
                '<p class="lead"',
                clean_content,
                count=1
            )

        # Build thread section HTML if it's a thread
        thread_html = ''
        is_thread = result.get('is_thread', False)
        thread_content_data = result.get('thread_content')

        if is_thread and thread_content_data:
            thread_posts = thread_content_data.get('thread_posts', [])
            if thread_posts:
                thread_html = '\n    <div class="thread-section">\n'
                thread_html += '        <h2>连续贴 (Thread)</h2>\n'
                thread_html += '        <div class="thread-posts">\n'

                for i, post in enumerate(thread_posts, 1):
                    post_content = post.get('content', '')
                    post_author = post.get('author', '')
                    post_timestamp = post.get('timestamp', '')
                    post_images = post.get('images', [])

                    thread_html += (
                        f'            <div class="thread-post" data-index="{i}">\n'
                        f'                <div class="thread-post-header">\n'
                        f'                    <span class="thread-post-number">#{i}</span>\n'
                    )

                    if post_author:
                        thread_html += (
                            f'                    <span class="thread-post-author">'
                            f'{post_author}</span>\n'
                        )

                    if post_timestamp:
                        thread_html += (
                            f'                    <span class="thread-post-time">'
                            f'{post_timestamp}</span>\n'
                        )

                    thread_html += '                </div>\n'
                    thread_html += (
                        '                <div class="thread-post-content">\n'
                    )

                    post_content_parts = post.get('content_parts', [])
                    if post_content_parts and len(post_content_parts) > 1:
                        for part in post_content_parts:
                            p_lang = part.get('lang', '')
                            p_text = part.get('text', '')
                            p_is_zh = (p_lang.startswith('zh')
                                       or any('\u4e00' <= c <= '\u9fff'
                                              for c in p_text))
                            p_label = '中文' if p_is_zh else 'English'
                            p_class = ('bilingual-zh' if p_is_zh
                                       else 'bilingual-en')
                            p_html = part.get('html', '')
                            if p_html:
                                clean_p = self._clean_html_content(
                                    p_html, local_images
                                )
                            else:
                                clean_p = f'<p>{p_text}</p>'
                            thread_html += (
                                f'                    <div class="bilingual-block {p_class}">'
                                f'<span class="lang-label">{p_label}</span>'
                                f'{clean_p}</div>\n'
                            )
                    else:
                        thread_html += (
                            f'                    <p>{post_content}</p>\n'
                        )

                    # Add images for this thread post
                    if post_images:
                        thread_html += '                    <div class="thread-post-images">\n'
                        for j, img_url in enumerate(post_images, 1):
                            if img_url in local_images:
                                img_info = local_images[img_url]
                                img_path = img_info.get('local_path', img_url)
                            else:
                                img_path = img_url
                            thread_html += (
                                f'                        <img src="{img_path}" '
                                f'alt="Thread图片 {i}-{j}" loading="lazy">\n'
                            )
                        thread_html += '                    </div>\n'

                    thread_html += '                </div>\n'
                    thread_html += '            </div>\n'

                thread_html += '        </div>\n'
                thread_html += '    </div>\n'

        # Build media section HTML
        media_html = ''
        has_media = images or video_urls or embedded_videos
        if has_media:
            media_html = '\n    <div class="media-section">\n        <h2>媒体</h2>\n'

            # Images with local paths or original URLs
            if images:
                media_html += '        <div class="images-grid">\n'
                for i, img_url in enumerate(images, 1):
                    # Use local path if available, otherwise original URL
                    if img_url in local_images:
                        img_info = local_images[img_url]
                        img_path = img_info.get('local_path', img_url)
                        alt_text = img_info.get('alt', f'图片 {i}')
                    else:
                        img_path = img_url
                        alt_text = f'图片 {i}'
                    media_html += (
                        f'            <div class="image-item">\n'
                        f'                <img src="{img_path}" '
                        f'alt="{alt_text}" loading="lazy">\n'
                        f'                <span class="image-label">图片 {i}</span>\n'
                        f'            </div>\n'
                    )
                media_html += '        </div>\n'

            # Native videos with player
            # Use local videos if available, otherwise use original URLs
            videos_to_show = local_videos if local_videos else video_urls
            if videos_to_show:
                media_html += '        <div class="videos-section">\n'
                for i, video_path in enumerate(videos_to_show, 1):
                    # Get just the filename for display
                    if '/' in video_path:
                        video_filename = os.path.basename(video_path)
                    else:
                        video_filename = video_path
                    media_html += (
                        f'            <div class="video-item">\n'
                        f'                <h3>视频 {i}</h3>\n'
                        f'                <video controls preload="metadata">\n'
                        f'                    <source src="videos/{video_filename}" '
                        f'type="video/mp4">\n'
                        f'                    您的浏览器不支持视频播放。\n'
                        f'                </video>\n'
                        f'            </div>\n'
                    )
                media_html += '        </div>\n'

            # Embedded videos (YouTube, Vimeo, etc.)
            if embedded_videos:
                media_html += '        <div class="embedded-videos-section">\n'
                for video in embedded_videos:
                    platform = video.get('type', 'video')
                    video_url = video.get('url', '')
                    video_title = video.get('title', '')
                    video_id = video.get('videoId', '')

                    media_html += (
                        '            <div class="embedded-video-item">\n'
                        f'                <h3>嵌入视频 ({platform})</h3>\n'
                    )

                    # Generate embed code based on platform
                    if platform == 'youtube' and video_id:
                        embed_url = (
                            f"https://www.youtube.com/embed/{video_id}"
                        )
                        media_html += (
                            '                <div class="video-embed">\n'
                            f'                    <iframe src="{embed_url}" '
                            'frameborder="0" allowfullscreen '
                            f'title="{video_title or "YouTube video"}">'
                            '</iframe>\n'
                            '                </div>\n'
                        )
                    elif platform == 'vimeo' and video_id:
                        embed_url = (
                            f"https://player.vimeo.com/video/{video_id}"
                        )
                        media_html += (
                            '                <div class="video-embed">\n'
                            f'                    <iframe src="{embed_url}" '
                            'frameborder="0" allowfullscreen '
                            f'title="{video_title or "Vimeo video"}">'
                            '</iframe>\n'
                            '                </div>\n'
                        )
                    else:
                        # Fallback: show as link
                        media_html += (
                            f'                <p><a href="{video_url}" '
                            'target="_blank">'
                            f'{video_title or video_url}</a></p>\n'
                        )

                    media_html += '            </div>\n'
                media_html += '        </div>\n'

            media_html += '    </div>\n'

        # Build external links section HTML
        links_html = ''
        if external_links:
            links_html = '\n    <div class="links-section">\n        <h2>外部链接</h2>\n'
            links_html += '        <div class="links-list">\n'

            for link in external_links:
                link_text = link.get('text', '')
                link_url = link.get('url', '')
                preview = link.get('preview', {})
                preview_title = preview.get('title', '')
                preview_desc = preview.get('description', '')
                preview_image = preview.get('image', '')

                links_html += (
                    f'            <a href="{link_url}" '
                    'target="_blank" class="link-card">\n'
                )

                if preview_image:
                    links_html += (
                        '                <div class="link-image">\n'
                        f'                    <img src="{preview_image}" '
                        'alt="" loading="lazy">\n'
                        '                </div>\n'
                    )

                links_html += '                <div class="link-content">\n'

                if preview_title:
                    links_html += (
                        '                    <h3 class="link-title">'
                        f'{preview_title}</h3>\n'
                    )
                elif link_text:
                    links_html += (
                        '                    <h3 class="link-title">'
                        f'{link_text}</h3>\n'
                    )

                if preview_desc:
                    desc = preview_desc[:150] + "..." if len(
                        preview_desc
                    ) > 150 else preview_desc
                    links_html += (
                        '                    <p class="link-description">'
                        f'{desc}</p>\n'
                    )

                links_html += (
                    '                    <span class="link-url">'
                    f'{link_url}</span>\n'
                )
                links_html += '                </div>\n'
                links_html += '            </a>\n'

            links_html += '        </div>\n    </div>\n'

        # Build full HTML document
        html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{display_title}</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
                         Helvetica, Arial, sans-serif;
            max-width: 800px;
            margin: 0 auto;
            padding: 20px;
            line-height: 1.8;
            background-color: #f7f9fa;
            color: #0f1419;
        }}
        .header {{
            background: #fff;
            border-radius: 16px;
            padding: 24px;
            margin-bottom: 20px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
        }}
        .article-header {{
            border-left: 4px solid #1d9bf0;
        }}
        .regular-header {{
            border-left: 4px solid #00ba7c;
        }}
        .post-type-badge {{
            display: inline-block;
            padding: 5px 14px;
            border-radius: 12px;
            font-size: 0.85em;
            font-weight: 600;
            text-transform: uppercase;
            margin-bottom: 12px;
        }}
        .post-type-badge.article {{
            background: #e8f5fe;
            color: #1d9bf0;
        }}
        .post-type-badge.regular {{
            background: #e0f7ed;
            color: #00ba7c;
        }}
        .header h1 {{
            font-size: 1.5em;
            margin: 0 0 16px 0;
            line-height: 1.3;
            color: #0f1419;
        }}
        .meta {{
            color: #536471;
            font-size: 0.9em;
        }}
        .meta p {{
            margin: 4px 0;
        }}
        .meta a {{
            color: #1d9bf0;
            text-decoration: none;
        }}
        .meta a:hover {{
            text-decoration: underline;
        }}
        .toc {{
            background: #fff;
            border-radius: 16px;
            padding: 24px;
            margin-bottom: 20px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
        }}
        .toc h2 {{
            font-size: 1em;
            font-weight: 600;
            margin: 0 0 12px 0;
            padding-bottom: 10px;
            border-bottom: 1px solid #e1e8ed;
            color: #536471;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}
        .toc ul {{
            list-style: none;
            padding: 0;
            margin: 0;
        }}
        .toc li {{
            margin: 0;
            padding: 6px 0;
        }}
        .toc-level-3 {{
            padding-left: 20px;
        }}
        .toc a {{
            color: #1d9bf0;
            text-decoration: none;
        }}
        .toc a:hover {{
            text-decoration: underline;
        }}
        .reading-time {{
            color: #1d9bf0;
            font-weight: 500;
        }}
        .content .lead {{
            font-size: 1.1em;
            line-height: 1.7;
            color: #1a1a1a;
            font-weight: 500;
            margin-bottom: 16px;
            padding-bottom: 16px;
            border-bottom: 1px solid #e1e8ed;
        }}
        .content {{
            background: #fff;
            border-radius: 16px;
            padding: 24px;
            margin-bottom: 20px;
            font-size: 1.05em;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
        }}
        .content p {{
            margin: 0 0 1em 0;
        }}
        .content h2 {{
            font-size: 1.3em;
            font-weight: 700;
            margin: 1.8em 0 1em 0;
            padding-left: 12px;
            border-left: 4px solid #1d9bf0;
            border-bottom: 1px solid #e1e8ed;
            padding-bottom: 8px;
            color: #0f1419;
        }}
        .content h3 {{
            font-size: 1.15em;
            font-weight: 600;
            margin: 1.5em 0 0.8em 0;
            padding-left: 10px;
            border-left: 2px solid #1d9bf0;
            color: #0f1419;
        }}
        .content a {{
            color: #1d9bf0;
            text-decoration: none;
        }}
        .content a:hover {{
            text-decoration: underline;
        }}
        .content strong {{
            font-weight: 600;
        }}
        .content em {{
            font-style: italic;
        }}
        .content img {{
            max-width: 100%;
            border-radius: 12px;
            margin: 15px 0;
            display: block;
        }}
        .content pre {{
            background: #1e1e1e;
            color: #d4d4d4;
            padding: 16px;
            border-radius: 8px;
            overflow-x: auto;
            margin: 15px 0;
        }}
        .content pre code {{
            font-family: 'SF Mono', Monaco, 'Courier New', monospace;
            font-size: 0.9em;
            line-height: 1.5;
            white-space: pre;
            white-space: pre-wrap;
            word-wrap: break-word;
            background: none;
            padding: 0;
            display: block;
            tab-size: 4;
        }}
        .content code {{
            background: #f4f4f4;
            padding: 2px 6px;
            border-radius: 4px;
            font-family: 'SF Mono', Monaco, monospace;
            font-size: 0.9em;
        }}
        .thread-section {{
            background: #fff;
            border-radius: 16px;
            padding: 24px;
            margin-bottom: 20px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
        }}
        .thread-section h2 {{
            font-size: 1.2em;
            margin: 0 0 16px 0;
            padding-bottom: 12px;
            border-bottom: 1px solid #e1e8ed;
            color: #1d9bf0;
        }}
        .thread-posts {{
            display: flex;
            flex-direction: column;
            gap: 16px;
        }}
        .thread-post {{
            background: #f7f9fa;
            border-radius: 12px;
            padding: 16px;
            border-left: 3px solid #1d9bf0;
        }}
        .thread-post-header {{
            display: flex;
            align-items: center;
            gap: 12px;
            margin-bottom: 12px;
            font-size: 0.9em;
        }}
        .thread-post-number {{
            background: #1d9bf0;
            color: #fff;
            padding: 2px 8px;
            border-radius: 12px;
            font-weight: 600;
            font-size: 0.85em;
        }}
        .thread-post-author {{
            color: #1d9bf0;
            font-weight: 600;
        }}
        .thread-post-time {{
            color: #536471;
            font-size: 0.85em;
        }}
        .thread-post-content {{
            color: #0f1419;
            line-height: 1.6;
        }}
        .thread-post-content p {{
            margin: 0 0 12px 0;
        }}
        .bilingual-block {{
            margin-bottom: 16px;
            padding: 12px 16px;
            border-radius: 8px;
            position: relative;
        }}
        .bilingual-zh {{
            background: #fef7f0;
            border-left: 3px solid #f77f00;
        }}
        .bilingual-en {{
            background: #f0f4ff;
            border-left: 3px solid #1d9bf0;
        }}
        .lang-label {{
            display: inline-block;
            font-size: 0.75em;
            font-weight: 600;
            padding: 1px 8px;
            border-radius: 10px;
            margin-bottom: 8px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}
        .bilingual-zh .lang-label {{
            background: #f77f00;
            color: #fff;
        }}
        .bilingual-en .lang-label {{
            background: #1d9bf0;
            color: #fff;
        }}
        .bilingual-block p {{
            margin: 0 0 8px 0;
        }}
        .bilingual-block p:last-child {{
            margin-bottom: 0;
        }}
        .thread-post-images {{
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
            gap: 8px;
            margin-top: 12px;
        }}
        .thread-post-images img {{
            width: 100%;
            height: auto;
            border-radius: 8px;
            object-fit: cover;
        }}
        .media-section {{
            background: #fff;
            border-radius: 16px;
            padding: 24px;
            margin-bottom: 20px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
        }}
        .media-section h2 {{
            font-size: 1.2em;
            margin: 0 0 16px 0;
            padding-bottom: 12px;
            border-bottom: 1px solid #e1e8ed;
        }}
        .images-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
            gap: 16px;
            margin-bottom: 20px;
        }}
        .image-item {{
            position: relative;
        }}
        .image-item img {{
            width: 100%;
            height: auto;
            border-radius: 12px;
            object-fit: cover;
        }}
        .image-label {{
            display: block;
            margin-top: 8px;
            font-size: 0.85em;
            color: #536471;
        }}
        .videos-section, .embedded-videos-section {{
            margin-top: 20px;
        }}
        .video-item, .embedded-video-item {{
            margin-bottom: 20px;
        }}
        .video-item h3, .embedded-video-item h3 {{
            font-size: 1em;
            margin: 0 0 12px 0;
            color: #536471;
        }}
        .video-item video {{
            width: 100%;
            max-height: 500px;
            border-radius: 12px;
        }}
        .video-embed {{
            position: relative;
            padding-bottom: 56.25%;
            height: 0;
            overflow: hidden;
            border-radius: 12px;
        }}
        .video-embed iframe {{
            position: absolute;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            border: none;
        }}
        .links-section {{
            background: #fff;
            border-radius: 16px;
            padding: 24px;
            margin-bottom: 20px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
        }}
        .links-section h2 {{
            font-size: 1.2em;
            margin: 0 0 16px 0;
            padding-bottom: 12px;
            border-bottom: 1px solid #e1e8ed;
        }}
        .links-list {{
            display: flex;
            flex-direction: column;
            gap: 12px;
        }}
        .link-card {{
            display: flex;
            gap: 16px;
            padding: 16px;
            border: 1px solid #e1e8ed;
            border-radius: 12px;
            text-decoration: none;
            color: inherit;
            transition: background-color 0.2s;
        }}
        .link-card:hover {{
            background-color: #f7f9fa;
        }}
        .link-image {{
            flex-shrink: 0;
            width: 100px;
            height: 100px;
            border-radius: 8px;
            overflow: hidden;
        }}
        .link-image img {{
            width: 100%;
            height: 100%;
            object-fit: cover;
        }}
        .link-content {{
            flex: 1;
            min-width: 0;
        }}
        .link-title {{
            font-size: 1em;
            font-weight: 600;
            margin: 0 0 8px 0;
            color: #0f1419;
        }}
        .link-description {{
            font-size: 0.9em;
            color: #536471;
            margin: 0 0 8px 0;
            line-height: 1.5;
        }}
        .link-url {{
            font-size: 0.85em;
            color: #1d9bf0;
        }}
        .footer {{
            text-align: center;
            padding: 20px;
            color: #536471;
            font-size: 0.85em;
        }}
        @media (max-width: 600px) {{
            body {{
                padding: 12px 16px;
                line-height: 1.9;
            }}
            .header {{
                border-radius: 6px;
                padding: 16px;
                margin-bottom: 16px;
                box-shadow: none;
                border-bottom: 1px solid #e1e8ed;
            }}
            .header h1 {{
                font-size: 1.4em;
                margin: 0 0 12px 0;
                line-height: 1.35;
            }}
            .meta {{
                font-size: 0.85em;
            }}
            .meta p {{
                margin: 2px 0;
            }}
            .content {{
                border-radius: 6px;
                padding: 16px;
                margin-bottom: 16px;
                box-shadow: none;
            }}
            .content p {{
                margin: 0 0 1.1em 0;
            }}
            .content h2 {{
                font-size: 1.2em;
                margin: 1.3em 0 0.7em 0;
                padding-left: 10px;
                border-left: 3px solid #1d9bf0;
            }}
            .content h3 {{
                font-size: 1.1em;
                margin: 1em 0 0.5em 0;
                padding-left: 8px;
                border-left: 2px solid #00ba7c;
            }}
            .toc {{
                border-radius: 6px;
                padding: 16px;
                margin-bottom: 16px;
                box-shadow: none;
            }}
            .toc h2 {{
                font-size: 0.95em;
            }}
            .content .lead {{
                font-size: 1.05em;
                padding-bottom: 12px;
                margin-bottom: 12px;
            }}
            .content pre {{
                padding: 12px;
                border-radius: 4px;
                margin: 12px 0;
            }}
            .content pre code {{
                font-size: 0.85em;
            }}
            .content img {{
                border-radius: 6px;
                margin: 12px 0;
            }}
            .thread-section {{
                border-radius: 6px;
                padding: 16px;
                margin-bottom: 16px;
                box-shadow: none;
            }}
            .thread-post {{
                border-radius: 6px;
                padding: 14px;
                border-left: 3px solid #1d9bf0;
            }}
            .thread-post-header {{
                flex-wrap: wrap;
                gap: 8px;
                margin-bottom: 10px;
            }}
            .thread-post-images {{
                grid-template-columns: 1fr;
                gap: 8px;
            }}
            .bilingual-block {{
                margin-bottom: 12px;
                padding: 10px 12px;
                border-radius: 4px;
            }}
            .media-section {{
                border-radius: 6px;
                padding: 16px;
                margin-bottom: 16px;
                box-shadow: none;
            }}
            .images-grid {{
                grid-template-columns: 1fr;
                gap: 12px;
            }}
            .image-item img {{
                border-radius: 6px;
            }}
            .link-card {{
                flex-direction: column;
                padding: 14px;
                border-radius: 6px;
            }}
            .link-image {{
                width: 100%;
                height: auto;
                max-height: 180px;
                border-radius: 4px;
            }}
            .links-section {{
                border-radius: 6px;
                padding: 16px;
                margin-bottom: 16px;
                box-shadow: none;
            }}
            .footer {{
                padding: 16px;
            }}
        }}
        @media print {{
            @page {{
                size: A4;
                margin: 2cm;
            }}
            body {{
                font-family: 'Noto Serif', Georgia, 'Times New Roman', serif;
                font-size: 16px;
                line-height: 1.9;
                color: #000;
                background: #fff;
                padding: 0;
                max-width: none;
            }}
            .header {{
                background: #fff;
                border-radius: 0;
                padding: 0 0 16px 0;
                margin-bottom: 20px;
                box-shadow: none;
                border-bottom: 2px solid #000;
            }}
            .article-header {{
                border-left: 3px solid #000;
            }}
            .regular-header {{
                border-left: 3px solid #000;
            }}
            .post-type-badge {{
                border-radius: 0;
                background: #000;
                color: #fff;
            }}
            .header h1 {{
                font-size: 1.5em;
                color: #000;
            }}
            .meta {{
                color: #333;
            }}
            .meta a {{
                color: #333;
            }}
            .content {{
                background: #fff;
                border-radius: 0;
                padding: 0;
                margin-bottom: 20px;
                box-shadow: none;
                font-size: 1em;
            }}
            .content h2 {{
                color: #000;
                padding-left: 10px;
                border-left: 3px solid #000;
                page-break-after: avoid;
            }}
            .content h3 {{
                color: #000;
                page-break-after: avoid;
            }}
            .toc {{
                background: #fff;
                border-radius: 0;
                padding: 0 0 12px 0;
                margin-bottom: 16px;
                box-shadow: none;
                border-bottom: 1px solid #000;
            }}
            .toc h2 {{
                color: #000;
                font-size: 0.95em;
                border-bottom: 1px solid #000;
            }}
            .toc a {{
                color: #000;
                text-decoration: none;
            }}
            .reading-time {{
                color: #333;
            }}
            .content .lead {{
                font-size: 1em;
                color: #000;
                font-weight: 500;
                border-bottom: 1px solid #ccc;
                padding-bottom: 12px;
                margin-bottom: 12px;
            }}
            .content a {{
                color: #000;
                text-decoration: none;
            }}
            .content a[href^="http"]:after {{
                content: " (" attr(href) ")";
                font-size: 0.8em;
                color: #555;
            }}
            .content img {{
                max-width: 100% !important;
                page-break-inside: avoid;
                border-radius: 0;
            }}
            .content pre {{
                background: #222;
                color: #ccc;
                border-radius: 0;
                border: 1px solid #000;
                page-break-inside: avoid;
            }}
            .content pre code {{
                font-family: 'Courier New', Courier, monospace;
                font-size: 14px;
            }}
            .content code {{
                background: #f0f0f0;
                border-radius: 0;
            }}
            .thread-section {{
                background: #fff;
                border-radius: 0;
                padding: 0;
                margin-bottom: 20px;
                box-shadow: none;
                border-top: 2px solid #000;
                padding-top: 16px;
            }}
            .thread-section h2 {{
                color: #000;
                border-bottom: 1px solid #000;
            }}
            .thread-post {{
                background: #fff;
                border-radius: 0;
                border-left: 3px solid #000;
                page-break-inside: avoid;
            }}
            .thread-post-number {{
                background: #000;
                color: #fff;
                border-radius: 0;
            }}
            .thread-post-author {{
                color: #000;
            }}
            .bilingual-block {{
                border-radius: 0;
                padding: 8px 12px;
                page-break-inside: avoid;
            }}
            .bilingual-zh {{
                background: #f5f5f5;
                border-left: 3px solid #333;
            }}
            .bilingual-en {{
                background: #f8f8f8;
                border-left: 3px solid #666;
            }}
            .bilingual-zh .lang-label {{
                background: #333;
                color: #fff;
                border-radius: 0;
            }}
            .bilingual-en .lang-label {{
                background: #666;
                color: #fff;
                border-radius: 0;
            }}
            .media-section {{
                background: #fff;
                border-radius: 0;
                padding: 0;
                margin-bottom: 20px;
                box-shadow: none;
                border-top: 2px solid #000;
                padding-top: 16px;
            }}
            .media-section h2 {{
                border-bottom: 1px solid #000;
            }}
            .images-grid {{
                grid-template-columns: 1fr;
                gap: 12px;
            }}
            .image-item img {{
                border-radius: 0;
                page-break-inside: avoid;
            }}
            .videos-section, .embedded-videos-section {{
                page-break-inside: avoid;
            }}
            .video-item video {{
                border-radius: 0;
            }}
            .video-embed {{
                border-radius: 0;
            }}
            .links-section {{
                background: #fff;
                border-radius: 0;
                padding: 0;
                margin-bottom: 20px;
                box-shadow: none;
                border-top: 2px solid #000;
                padding-top: 16px;
            }}
            .links-section h2 {{
                border-bottom: 1px solid #000;
            }}
            .link-card {{
                border: 1px solid #333;
                border-radius: 0;
                page-break-inside: avoid;
            }}
            .link-card:hover {{
                background-color: #fff;
            }}
            .link-image {{
                border-radius: 0;
            }}
            .link-title {{
                color: #000;
            }}
            .link-description {{
                color: #333;
            }}
            .link-url {{
                color: #333;
            }}
            .footer {{
                border-top: 1px solid #ccc;
                color: #666;
            }}
        }}
    </style>
</head>
<body>
{header_html}
{toc_html}    <div class="content">
{clean_content}
    </div>
{thread_html}{media_html}{links_html}    <div class="footer">
        <p>由 YTBot 自动提取</p>
    </div>
</body>
</html>'''

        return html

    def _detect_image_extension(self, img_url: str, content_type: str = None) -> str:
        """
        Detect image extension from URL and content type.

        Supports: webp, png, jpg, jpeg, gif

        Args:
            img_url: The image URL
            content_type: HTTP Content-Type header
                (optional)

        Returns:
            File extension including the dot (e.g., '.jpg')
        """
        # Check URL patterns first
        url_lower = img_url.lower()

        # Check for format parameter in URL
        if 'format=webp' in url_lower or 'format=web' in url_lower:
            return '.webp'
        if 'format=png' in url_lower:
            return '.png'
        if 'format=jpg' in url_lower or 'format=jpeg' in url_lower:
            return '.jpg'
        if 'format=gif' in url_lower:
            return '.gif'

        # Check file extension in URL path
        import re
        ext_match = re.search(r'\.(webp|png|jpe?g|gif)(?:\?|#|$)', url_lower)
        if ext_match:
            ext = ext_match.group(1)
            if ext == 'jpeg':
                return '.jpg'
            return f'.{ext}'

        # Check content type if available
        if content_type:
            ct_lower = content_type.lower()
            if 'webp' in ct_lower:
                return '.webp'
            if 'png' in ct_lower:
                return '.png'
            if 'gif' in ct_lower:
                return '.gif'
            if 'jpeg' in ct_lower or 'jpg' in ct_lower:
                return '.jpg'

        # Default to jpg
        return '.jpg'

    async def _download_images(
        self,
        image_urls: list,
        images_dir: str,
        image_metadata: List[Dict] = None
    ) -> Dict[str, Dict[str, Any]]:
        """
        Download images to local directory with metadata.
        Uses parallel download with concurrency limit to avoid rate limiting.

        Args:
            image_urls: List of image URLs to download
            images_dir: Directory to save images
            image_metadata: Optional list of image metadata dicts

        Returns:
            Dict mapping original URL to dict with:
                - local_path: Relative path to saved image
                - filename: Saved filename
                - size: File size in bytes
                - width: Image width (if available)
                - height: Image height (if available)
                - format: Image format
        """
        from ytbot.utils.async_utils import gather_with_concurrency

        local_images = {}
        metadata_map = {}
        if image_metadata:
            for meta in image_metadata:
                url = meta.get('url')
                if url:
                    metadata_map[url] = meta

        if not image_urls:
            return local_images

        async def download_single_image(img_url: str, index: int) -> Dict[str, Any]:
            try:
                timeout = aiohttp.ClientTimeout(total=30)
                async with aiohttp.ClientSession() as session:
                    async with session.get(img_url, timeout=timeout) as response:
                        if response.status == 200:
                            content = await response.read()
                            content_type = response.headers.get(
                                'Content-Type', ''
                            )

                            ext = self._detect_image_extension(
                                img_url, content_type
                            )

                            local_filename = f'image_{index + 1}{ext}'
                            local_path = os.path.join(
                                images_dir, local_filename
                            )

                            with open(local_path, 'wb') as f:
                                f.write(content)

                            file_size = len(content)
                            meta = metadata_map.get(img_url, {})

                            result = {
                                img_url: {
                                    'local_path': (
                                        f'images/{local_filename}'
                                    ),
                                    'filename': local_filename,
                                    'size': file_size,
                                    'width': meta.get('width'),
                                    'height': meta.get('height'),
                                    'format': ext.lstrip('.'),
                                    'alt': meta.get('alt')
                                }
                            }

                            logger.info(
                                f"Downloaded image "
                                f"{index + 1}/{len(image_urls)}: "
                                f"{local_filename} ({file_size} bytes)"
                            )
                            return result
                        else:
                            logger.warning(
                                f"Image download failed with "
                                f"status {response.status}: {img_url}"
                            )
            except Exception as e:
                logger.warning(f"Failed to download image {img_url}: {e}")
            return {}

        results = await gather_with_concurrency(
            3,
            *[download_single_image(url, i) for i, url in enumerate(image_urls)]
        )

        for result in results:
            local_images.update(result)

        return local_images

    async def download_video(self, video_url: str, output_path: str) -> Optional[str]:
        """
        Download X/Twitter native video using yt-dlp.

        Args:
            video_url: The video URL to download
            output_path: Directory to save the video

        Returns:
            Path to the downloaded video file, or None if download failed
        """
        try:
            # Check if yt-dlp is available
            try:
                import yt_dlp
            except ImportError:
                logger.error(
                    "yt-dlp not installed. Cannot download videos. "
                    "Install with: pip install yt-dlp"
                )
                return None

            # Ensure output directory exists
            os.makedirs(output_path, exist_ok=True)

            # Generate output filename
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            output_template = os.path.join(
                output_path, f'video_{timestamp}.%(ext)s'
            )

            # Configure yt-dlp options
            ydl_opts = {
                'format': 'best',  # Download best quality
                'outtmpl': output_template,
                'quiet': True,
                'no_warnings': True,
                'cookiesfrombrowser': None,  # Don't use browser cookies
                'http_headers': {
                    'User-Agent': (
                        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                        'AppleWebKit/537.36 (KHTML, like Gecko) '
                        'Chrome/120.0.0.0 Safari/537.36'
                    ),
                    'Accept': '*/*',
                    'Accept-Language': 'en-US,en;q=0.9',
                },
            }

            # Skip blob URLs (browser-local, not accessible by yt-dlp)
            if video_url.startswith('blob:'):
                logger.warning(f"Skipping blob URL (not accessible): {video_url}")
                return None

            # Skip Twitter/X article URLs (not supported by yt-dlp)
            if '/i/article/' in video_url:
                logger.warning(f"Skipping Twitter/X article URL (not a video): {video_url}")
                return None

            logger.info(f"Starting video download: {video_url}")

            # Run yt-dlp in thread pool to avoid blocking the event loop
            def _sync_download():
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(video_url, download=True)
                    if info:
                        filename = ydl.prepare_filename(info)
                        logger.info(f"yt-dlp prepared filename: {filename}")
                        logger.info(f"Output directory: {output_path}")
                        logger.info(f"Directory contents: {os.listdir(output_path)}")
                        if os.path.exists(filename):
                            logger.info(f"Video downloaded successfully: {filename}")
                            return filename
                        else:
                            base_path = filename.rsplit('.', 1)[0]
                            for ext in ['.mp4', '.webm', '.mkv', '.mov']:
                                alt_path = base_path + ext
                                if os.path.exists(alt_path):
                                    logger.info(f"Video downloaded successfully: {alt_path}")
                                    return alt_path
                logger.error(f"Video download failed: {video_url}")
                return None

            filename = await asyncio.to_thread(_sync_download)
            return filename

        except Exception as e:
            error_msg = str(e)
            # Check if it's an unsupported URL error (e.g., Twitter/X article URLs)
            if "Unsupported URL" in error_msg:
                logger.warning(f"URL not supported by yt-dlp, skipping: {video_url}")
                return None
            logger.error(f"Error downloading video {video_url}: {e}")
            return None

    def _clean_html_content(self, html: str, local_images: Dict[str, Any] = None) -> str:
        """Clean HTML content and extract meaningful text with formatting"""
        import re
        local_images = local_images or {}

        for orig_url, local_path in local_images.items():
            # Handle both Dict[str, str] and Dict[str, Dict] formats
            if isinstance(local_path, dict):
                path_value = local_path.get('local_path', orig_url)
            else:
                path_value = local_path
            html = html.replace(orig_url, path_value)

        html = self._convert_x_code_blocks_to_pre(html)

        html = re.sub(r'<section[^>]*>', '', html)
        html = re.sub(r'</section>', '\n\n', html)
        html = re.sub(r'<div[^>]*>', '', html)
        html = re.sub(r'</div>', '\n', html)
        html = re.sub(r'<svg[^>]*>.*?</svg>', '', html, flags=re.DOTALL)
        html = re.sub(r'<button[^>]*>.*?</button>', '', html, flags=re.DOTALL)
        html = re.sub(r'<path[^>]*>.*?</path>', '', html, flags=re.DOTALL)
        html = re.sub(r'<g[^>]*>.*?</g>', '', html, flags=re.DOTALL)

        html = re.sub(
            r'<h2[^>]*>([^<]*)</h2>',
            r'\n\n<h2>\1</h2>\n\n',
            html
        )
        html = re.sub(
            r'<h3[^>]*>([^<]*)</h3>',
            r'\n\n<h3>\1</h3>\n\n',
            html
        )

        html = re.sub(
            r'<a [^>]*href="([^"]*)"[^>]*>([^<]*)</a>',
            r'<a href="\1">\2</a>',
            html
        )

        html = self._preserve_pre_blocks(html, lambda h: re.sub(r'<br\s*/?>', '\n', h))
        html = self._preserve_pre_blocks(html, lambda h: re.sub(r'<p[^>]*>', '\n\n', h))
        html = self._preserve_pre_blocks(html, lambda h: re.sub(r'</p>', '\n\n', h))

        html = re.sub(r'<img[^>]*>', r'\n\n\g<0>\n\n', html)

        html = re.sub(r'\n\s*\n\s*\n+', '\n\n', html)
        # 保护pre块后移除行首空白，避免代码缩进丢失
        html = self._preserve_pre_blocks(
            html, lambda h: re.sub(r'^\s+', '', h, flags=re.MULTILINE)
        )

        lines = html.split('\n')
        formatted_lines = []
        in_code_block = False
        code_block_content = []

        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue

            if stripped.startswith('<pre') and '<code' in stripped:
                in_code_block = True
                code_block_content = [stripped]
                continue

            if in_code_block:
                # 在代码块内，保留原始行（包括缩进），不调用strip()
                if '</code></pre>' in stripped or '</pre>' in stripped:
                    in_code_block = False
                    code_block_content.append(line)
                    full_block = '\n'.join(code_block_content)
                    formatted_lines.append('')
                    formatted_lines.append(full_block)
                    formatted_lines.append('')
                    code_block_content = []
                else:
                    # 保留代码块内的原始行，保留缩进
                    code_block_content.append(line)
                continue

            if stripped.startswith('<h2>') or stripped.startswith('<h3>'):
                formatted_lines.append('')
                formatted_lines.append(stripped)
                formatted_lines.append('')
            elif stripped.startswith('<img'):
                formatted_lines.append('')
                formatted_lines.append(stripped)
                formatted_lines.append('')
            elif stripped.startswith('<pre'):
                formatted_lines.append('')
                formatted_lines.append(stripped)
                formatted_lines.append('')
            elif re.match(r'^[一二三四五六七八九十\d]+[、\.）\)]\s*', stripped):
                formatted_lines.append('')
                formatted_lines.append(f'<p>{stripped}</p>')
                formatted_lines.append('')
            elif re.match(r'^[（(]\d+[)）]\s*', stripped):
                formatted_lines.append('')
                formatted_lines.append(f'<p>{stripped}</p>')
                formatted_lines.append('')
            elif stripped.startswith('●') or stripped.startswith('•'):
                formatted_lines.append('')
                formatted_lines.append(f'<p>{stripped}</p>')
                formatted_lines.append('')
            else:
                formatted_lines.append(f'<p>{stripped}</p>')

        result = '\n'.join(formatted_lines)
        result = re.sub(r'\n\s*\n\s*\n+', '\n\n', result)
        return result.strip()

    def _convert_x_code_blocks_to_pre(self, html_content: str) -> str:
        """Convert X's special code block format to standard pre/code blocks"""
        import re
        import html as html_module
        import json

        def clean_code_content(code_str):
            """Clean code content by removing HTML tags and unescaping"""
            code_str = html_module.unescape(code_str)
            code_str = re.sub(r'<span[^>]*>', '', code_str)
            code_str = re.sub(r'</span>', '', code_str)
            code_str = re.sub(r'<[^>]+>', '', code_str)
            # 只去除每行的首尾空白，但保留行内的缩进
            lines = code_str.split('\n')
            cleaned_lines = [line.rstrip() for line in lines]
            # 去除首尾的空白行，但保留代码块内的缩进
            while cleaned_lines and not cleaned_lines[0].strip():
                cleaned_lines.pop(0)
            while cleaned_lines and not cleaned_lines[-1].strip():
                cleaned_lines.pop()
            return '\n'.join(cleaned_lines)

        def format_code_with_indent(code_str, language):
            """Format code with proper indentation based on language"""
            if language == 'json':
                try:
                    parsed = json.loads(code_str)
                    return json.dumps(parsed, indent=4, ensure_ascii=False)
                except (json.JSONDecodeError, ValueError):
                    pass

            lines = code_str.split('\n')
            formatted_lines = []
            indent_level = 0
            indent_str = '    '
            
            for line in lines:
                stripped = line.strip()
                if not stripped:
                    formatted_lines.append('')
                    continue
                
                close_brackets = sum(1 for c in stripped if c in '}]')
                open_brackets = sum(1 for c in stripped if c in '{[')
                
                if stripped.startswith('}') or stripped.startswith(']'):
                    indent_level = max(0, indent_level - close_brackets)
                
                formatted_lines.append(indent_str * indent_level + stripped)
                
                if stripped.endswith('{') or stripped.endswith('['):
                    indent_level += open_brackets
                elif open_brackets > close_brackets:
                    indent_level += (open_brackets - close_brackets)
            
            return '\n'.join(formatted_lines)

        def process_pre_code_blocks(html_str):
            """Process existing pre/code blocks"""
            result = html_str
            pattern = r'(<pre[^>]*><code[^>]*class="language-(\w+)"[^>]*>)(.*?)(</code></pre>)'
            
            def replace_match(match):
                lang = match.group(2)
                code_content = match.group(3)
                
                cleaned = clean_code_content(code_content)
                formatted = format_code_with_indent(cleaned, lang)
                escaped = html_module.escape(formatted)
                
                return f'<pre><code class="language-{lang}">{escaped}</code></pre>'
            
            return re.sub(pattern, replace_match, result, flags=re.DOTALL)

        def remove_language_label_before_pre(html_str):
            """Remove language label <p>json</p> before pre blocks"""
            pattern = r'<p>(\w+)</p>\s*(<pre[^>]*><code[^>]*class="language-\1"[^>]*>)'
            return re.sub(pattern, r'\2', html_str)

        html_content = process_pre_code_blocks(html_content)
        html_content = remove_language_label_before_pre(html_content)

        return html_content

    def _preserve_pre_blocks(self, html_content: str, transform_func) -> str:
        """Apply transform function while preserving pre blocks"""
        import re

        pre_blocks = []
        placeholder_idx = [0]

        def save_pre_block(match):
            placeholder = f'__PRE_BLOCK_{placeholder_idx[0]}__'
            pre_blocks.append((placeholder, match.group(0)))
            placeholder_idx[0] += 1
            return placeholder

        html_content = re.sub(r'<pre[^>]*>.*?</pre>', save_pre_block, html_content, flags=re.DOTALL)

        html_content = transform_func(html_content)

        for placeholder, block in pre_blocks:
            html_content = html_content.replace(placeholder, block)

        return html_content

    def _generate_markdown(self, result: Dict[str, Any]) -> str:
        """
        Generate formatted Markdown from scraped tweet data using unified template.

        Template structure:
        - Header: title, author, publish time, original URL, post type
        - Body: content with preserved formatting
        - Media section: images and videos
        - External links section: links with preview info
        """
        # Get basic info
        post_type = result.get('post_type', 'regular')
        article_title = result.get('article_title', '')
        url = result.get('url', '')

        # Determine title based on post type
        if post_type == 'article' and article_title:
            title = article_title
        else:
            title = result.get('title', 'X Post')

        # Get author and publish time from metadata
        author = result.get('author', '')
        if not author:
            # Fallback: extract from URL
            author_match = re.search(r'(twitter|x)\.com/(\w+)/status/', url)
            author = f"@{author_match.group(2)}" if author_match else "@unknown"

        publish_time = result.get('publish_time', '')
        if not publish_time:
            publish_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        # Build header section
        markdown = f"# {title}\n\n"
        markdown += f"> 作者: {author}\n"
        markdown += f"> 时间: {publish_time}\n"
        markdown += f"> 原文: {url}\n"
        markdown += f"> 类型: {post_type}\n\n"
        markdown += "---\n\n"

        # Build body content
        html_content = result.get('html', '')
        if html_content:
            formatted_content = self._html_to_markdown(html_content)
        else:
            content = result.get('content', '')
            if content:
                formatted_content = self.extractor.convert_to_markdown({
                    'text': content,
                    'formats': result.get('formats', []),
                    'codeBlocks': result.get('codeBlocks', [])
                })
                formatted_content = self._format_paragraphs(formatted_content)
            else:
                formatted_content = ''

        markdown += formatted_content
        markdown += "\n\n---\n\n"

        # Build media section
        images = result.get('images', [])
        video_urls = result.get('video_urls', [])
        embedded_videos = result.get('embedded_videos', [])

        has_media = images or video_urls or embedded_videos
        if has_media:
            markdown += "## 媒体\n\n"

            # Images
            if images:
                for i, img_url in enumerate(images, 1):
                    markdown += f"**图片 {i}**: ![图片 {i}]({img_url})\n\n"

            # Native videos
            if video_urls:
                for i, video_url in enumerate(video_urls, 1):
                    markdown += f"**视频 {i}**: [{video_url}]({video_url})\n\n"

            # Embedded videos (YouTube, Vimeo, etc.)
            if embedded_videos:
                for video in embedded_videos:
                    platform = video.get('type', 'video')
                    video_url = video.get('url', '')
                    video_title = video.get('title', '')
                    if video_title:
                        markdown += f"**嵌入视频 ({platform})**: "
                        markdown += f"[{video_title}]({video_url})\n\n"
                    else:
                        markdown += f"**嵌入视频 ({platform})**: "
                        markdown += f"[{video_url}]({video_url})\n\n"

            markdown += "\n"

        # Build external links section
        external_links = result.get('external_links', [])
        if external_links:
            markdown += "## 外部链接\n\n"

            for link in external_links:
                link_text = link.get('text', '')
                link_url = link.get('url', '')

                if link_text and link_url:
                    markdown += f"- [{link_text}]({link_url})\n"
                elif link_url:
                    markdown += f"- [{link_url}]({link_url})\n"

                # Add preview info if available
                preview = link.get('preview', {})
                if preview:
                    preview_title = preview.get('title', '')
                    preview_desc = preview.get('description', '')
                    if preview_title:
                        markdown += f"  - 标题: {preview_title}\n"
                    if preview_desc:
                        # Truncate description if too long
                        if len(preview_desc) > 100:
                            desc = preview_desc[:100] + "..."
                        else:
                            desc = preview_desc
                        markdown += f"  - 描述: {desc}\n"

            markdown += "\n"

        return markdown

    def _html_to_markdown(self, html: str) -> str:
        """Convert HTML content to Markdown with proper formatting"""
        from html.parser import HTMLParser

        class TwitterHTMLParser(HTMLParser):
            def __init__(self):
                super().__init__()
                self.result = []
                self.current_text = []
                self.in_paragraph = False
                self.in_link = False
                self.link_href = None
                self.link_text = []
                self.in_bold = False
                self.in_italic = False
                self.in_code = False
                self.list_items = []
                self.in_list = False

            def handle_starttag(self, tag, attrs):
                attrs_dict = dict(attrs)
                if tag in ('br',):
                    if self.current_text:
                        self.result.append(''.join(self.current_text))
                        self.current_text = []
                    self.result.append('')
                elif tag in ('p', 'div'):
                    if self.current_text:
                        self.result.append(''.join(self.current_text))
                        self.current_text = []
                    self.in_paragraph = True
                elif tag == 'a':
                    self.in_link = True
                    self.link_href = attrs_dict.get('href', '')
                    self.link_text = []
                elif tag in ('strong', 'b'):
                    self.in_bold = True
                elif tag in ('em', 'i'):
                    self.in_italic = True
                elif tag == 'code':
                    self.in_code = True
                elif tag == 'li':
                    self.in_list = True

            def handle_endtag(self, tag):
                if tag in ('p', 'div'):
                    if self.current_text:
                        self.result.append(''.join(self.current_text))
                        self.current_text = []
                    self.result.append('')
                    self.in_paragraph = False
                elif tag == 'a':
                    self.in_link = False
                    text = ''.join(self.link_text)
                    if self.link_href and text:
                        self.current_text.append(f'[{text}]({self.link_href})')
                    self.link_text = []
                    self.link_href = None
                elif tag in ('strong', 'b'):
                    self.in_bold = False
                elif tag in ('em', 'i'):
                    self.in_italic = False
                elif tag == 'code':
                    self.in_code = False
                elif tag == 'li':
                    self.in_list = False

            def handle_data(self, data):
                if self.in_link:
                    self.link_text.append(data)
                else:
                    text = data
                    if self.in_bold:
                        text = f'**{text}**'
                    if self.in_italic:
                        text = f'*{text}*'
                    if self.in_code:
                        text = f'`{text}`'
                    self.current_text.append(text)

        parser = TwitterHTMLParser()
        try:
            parser.feed(html)
            if parser.current_text:
                parser.result.append(''.join(parser.current_text))
        except Exception:
            pass

        lines = []
        for line in parser.result:
            line = line.strip()
            if line:
                lines.append(line)
            elif lines and lines[-1]:
                lines.append('')

        text = '\n'.join(lines)
        text = self._format_paragraphs(text)
        return text

    def _format_paragraphs(self, text: str) -> str:
        """Format text with proper paragraphs and sections"""
        lines = text.split('\n')
        formatted_lines = []
        prev_was_empty = True

        for line in lines:
            stripped = line.strip()
            if not stripped:
                if not prev_was_empty:
                    formatted_lines.append('')
                    prev_was_empty = True
                continue

            if self._is_list_item(stripped):
                formatted_lines.append(f"- {stripped.lstrip('- •·').strip()}")
                prev_was_empty = False
            else:
                formatted_lines.append(stripped)
                prev_was_empty = False

        return '\n'.join(formatted_lines)

    def _is_section_header(self, line: str) -> bool:
        """Check if line looks like a section header"""
        return False

    def _is_list_item(self, line: str) -> bool:
        """Check if line is a list item"""
        import re
        list_patterns = [
            r'^[\-\*\+]\s+',
            r'^\d+[\.\)]\s+',
            r'^[一二三四五六七八九十]+[\.\)]\s+',
            r'^[•·]\s+',
            r'^安全性[：:]',
            r'^性能[：:]',
            r'^集成性[：:]',
            r'^体验感[：:]',
            r'^你的背景[：:]',
            r'^你的偏好[：:]',
            r'^你的目标[：:]',
            r'^当天天气$',
            r'^热门新闻$',
            r'^待办事项',
            r'^编码\s*[→→]',
            r'^找新闻\s*[→→]',
            r'^搜索网络\s*[→→]',
        ]
        for pattern in list_patterns:
            if re.match(pattern, line):
                return True
        return False

    def get_supported_formats(self, url: str) -> List[Dict[str, Any]]:
        """Get available download formats for Twitter content"""
        formats = [
            {
                "format_id": "markdown",
                "format": "markdown",
                "description": "Tweet content as Markdown with formatting",
                "content_type": ContentType.TEXT.value
            },
            {
                "format_id": "text",
                "format": "text",
                "description": "Plain text content",
                "content_type": ContentType.TEXT.value
            }
        ]

        # Check if tweet has images
        # In a real implementation, we'd check the actual tweet
        formats.append({
            "format_id": "images",
            "format": "images",
            "description": "Download all images from tweet",
            "content_type": ContentType.IMAGE.value
        })

        return formats

    async def cleanup(self):
        """Cleanup resources"""
        await self.extractor.close_browser()


# Example of how to register the new platform
async def register_twitter_platform(download_service):
    """Register Twitter platform with the download service"""
    twitter_handler = TwitterHandler()
    download_service.platform_manager.register_handler(twitter_handler)

    logger.info("Twitter/X platform handler registered")
    logger.info(f"Supported platforms: {download_service.get_supported_platforms()}")


async def demo_twitter_download():
    """Demonstrate Twitter content download"""
    print("Twitter/X Platform Handler Demo")
    print("=" * 40)

    # Create handler
    twitter_handler = TwitterHandler()

    # Test URLs
    test_urls = [
        "https://twitter.com/elonmusk/status/1234567890",
        "https://x.com/user/status/1234567890",
    ]

    for url in test_urls:
        print(f"\nTesting URL: {url}")

        # Check if handler can process
        can_handle = twitter_handler.can_handle(url)
        print(f"   Can handle: {'Yes' if can_handle else 'No'}")

        if can_handle:
            # Get content info
            content_info = await twitter_handler.get_content_info(url)
            if content_info:
                print(f"   Title: {content_info.title}")
                print(f"   Type: {content_info.content_type.value}")
                print(f"   Author: {content_info.uploader}")

                # Get available formats
                formats = twitter_handler.get_supported_formats(url)
                print(f"   Available formats: {len(formats)}")
                for fmt in formats:
                    print(f"     - {fmt['format']}: {fmt['description']}")

    # Cleanup
    await twitter_handler.cleanup()
    print("\nTwitter platform handler demo completed!")


if __name__ == "__main__":
    asyncio.run(demo_twitter_download())
