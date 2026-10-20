"""Browser lifecycle for Twitter/X content extraction.

Contains the browser-installation helpers and ``BrowserMixin``: standalone
/ shared-browser lifecycle, cookie loading, login detection, HTTP session
and anonymous retry. Extracted verbatim from the former monolithic
``platforms/twitter.py``; methods operate on instance state provided by
the composed ``TwitterContentExtractor`` class.
"""

import os
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse, urlunparse

from ytbot.core.enhanced_logger import get_logger

logger = get_logger("ytbot.platforms.twitter")

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

# Optional aiohttp (used for HTTP session typing / reuse)
aiohttp = None
try:
    import aiohttp
except ImportError:
    pass


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


class BrowserMixin:
    """Browser lifecycle methods for TwitterContentExtractor (extracted)."""


    def _standalone_alive(self) -> bool:
        """Whether the standalone browser process is still connected."""
        if self.browser is None:
            return False
        try:
            return bool(self.browser.is_connected())
        except Exception:
            return False

    async def _dispose_standalone(self) -> None:
        """Close standalone (non-shared) context/browser/playwright.

        Errors from already-dead targets are swallowed: this is also the
        reset path after a browser crash. Context is closed before the
        browser (closing the browser already kills its contexts).
        """
        if self.context is not None:
            try:
                await self.context.close()
            except Exception as e:
                logger.debug(f"Error closing standalone context: {e}")
            self.context = None
        if self.browser is not None:
            try:
                await self.browser.close()
            except Exception as e:
                logger.debug(f"Error closing standalone browser: {e}")
            self.browser = None
        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception as e:
                logger.debug(f"Error stopping playwright: {e}")
            self._playwright = None
        self._using_shared_browser = False

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
            # Track the driver before launch so a launch failure is cleaned
            # up by _dispose_standalone().
            self._playwright = pw
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
            # This standalone browser is one-off: scrape_tweet's finally
            # tears it down so it can never leak into the next scrape.
            self._ephemeral_browser = True
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
                logger.warning(
                    "Anonymous access returned 200 but no content"
                )
            else:
                logger.warning(
                    f"Anonymous access also failed: {status}"
                )

            # Anonymous access unusable: destroy the one-off browser and
            # let scrape_tweet return a proper failure instead of parsing
            # an error page.
            try:
                await new_page.close()
            except Exception:
                pass
            await self._dispose_standalone()
            self._ephemeral_browser = False
            return None

        except Exception as e:
            logger.error(f"Error during anonymous retry: {e}")
            await self._dispose_standalone()
            self._ephemeral_browser = False
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

        # A crashed standalone browser must not be mistaken for a healthy
        # instance: dispose its zombie objects before choosing a path.
        if (
            self.browser is not None
            and not self._using_shared_browser
            and not self._standalone_alive()
        ):
            logger.warning(
                "Standalone browser is disconnected; cleaning up before init"
            )
            await self._dispose_standalone()
            self._ephemeral_browser = False

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

            # Replace any shared context carried over from enumeration or
            # a previous scrape instead of accumulating contexts inside
            # one long-lived browser process.
            if self.context is not None and self._using_shared_browser:
                await manager.close_context(self.context)
                self.context = None

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

    async def reset_browser(self):
        """Drop every dead browser/context reference so the next
        initialize_browser() builds a fresh, connected instance."""
        if self._using_shared_browser:
            from ytbot.core.browser_manager import get_browser_manager
            manager = await get_browser_manager()
            self.context = None
            self._using_shared_browser = False
            await manager.reset_if_disconnected()
        else:
            await self._dispose_standalone()
            self._ephemeral_browser = False

    async def _initialize_new_browser(self):
        """Initialize a new standalone browser instance.

        Any pre-existing standalone instance (including a crashed one) is
        fully torn down first — never silently reused.
        """
        check_and_install_browser()

        if (
            self.browser is not None
            or self.context is not None
            or self._playwright is not None
        ):
            if self._using_shared_browser:
                # Only the context belongs to us; the shared browser is
                # managed by BrowserManager.
                if self.context is not None:
                    from ytbot.core.browser_manager import BrowserManager
                    mgr = await BrowserManager.get_instance()
                    await mgr.close_context(self.context)
                self.context = None
                self._using_shared_browser = False
            else:
                await self._dispose_standalone()

        self._ephemeral_browser = False
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
            # Return the per-use context to the manager so it does not
            # accumulate in the shared browser process.
            from ytbot.core.browser_manager import BrowserManager
            browser_mgr = await BrowserManager.get_instance()
            if self.context:
                await browser_mgr.close_context(self.context)
            self.context = None
            self._using_shared_browser = False
            return

        await self._dispose_standalone()
        self._ephemeral_browser = False

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
