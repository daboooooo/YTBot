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
import tempfile
import os
from typing import Dict, Any, Optional, List
from datetime import datetime

from ytbot.platforms.base import PlatformHandler, ContentInfo, ContentType, DownloadResult
from ytbot.core.logger import get_logger
from ytbot.services.storage_service import StorageService

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

    async def initialize_browser(self):
        """Initialize Playwright browser with anti-detection measures"""
        if not PLAYWRIGHT_AVAILABLE:
            raise RuntimeError(
                "Playwright not installed. "
                "Install with: pip install playwright && playwright install chromium"
            )

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
            self.context = await self.browser.new_context(
                user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                viewport={'width': 1920, 'height': 1080},
                timezone_id='Asia/Shanghai',
                locale='zh-CN,zh;q=0.9,en;q=0.8',
                geolocation={'longitude': 121.4737, 'latitude': 31.2304},
                permissions=['geolocation']
            )

            # Inject anti-detection scripts
            await self.context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined,
                });

                Object.defineProperty(navigator, 'plugins', {
                    get: () => ({
                        length: 3,
                        0: { filename: 'internal-pdf-viewer' },
                        1: { filename: 'adsfk-plugin' },
                        2: { filename: 'internal-nacl-plugin' },
                        refresh: () => {},
                    }),
                });

                Object.defineProperty(navigator, 'languages', {
                    get: () => ['zh-CN', 'zh', 'en'],
                });
            """)

    async def close_browser(self):
        """Close the browser instance and cleanup resources"""
        if self.browser:
            await self.browser.close()
            self.browser = None
        if self.context:
            await self.context.close()
            self.context = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None

    async def expand_long_tweet(self, page) -> bool:
        """Click 'Show more' button to expand long tweets"""
        expand_selectors = [
            'div[role="button"]:has-text("显示更多")',
            'div[role="button"]:has-text("Show more")',
            '[data-testid="tweet-text-show-more-link"]',
            'span:has-text("显示更多")',
            'span:has-text("Show more")'
        ]

        expanded = False
        max_attempts = 5  # 最多尝试5次展开

        for attempt in range(max_attempts):
            found_button = False
            for selector in expand_selectors:
                try:
                    btn = await page.query_selector(selector)
                    if btn:
                        await btn.click()
                        await page.wait_for_timeout(1500)
                        logger.info(
                            f"Expanded long tweet content (attempt {attempt + 1})"
                        )
                        expanded = True
                        found_button = True
                        break
                except Exception:
                    continue

            if not found_button:
                break

        return expanded

    async def extract_formatted_content(self, page, base_url: str) -> Dict[str, Any]:
        """Extract tweet content with formatting information"""
        return await page.evaluate("""
            (base) => {
                const tweetElement = document.querySelector('[data-testid="tweetText"]') ||
                                     document.querySelector('article [lang]') ||
                                     document.querySelector('article');

                if (!tweetElement) return { text: '', html: '', formats: [], images: [], embeddedContent: [] };

                const images = [];
                const imgElements = document.querySelectorAll('[data-testid="tweetPhoto"] img, img[src*="pbs.twimg.com/media"]');
                imgElements.forEach(img => {
                    let src = img.getAttribute('src') || img.getAttribute('data-src');
                    if (src) {
                        if (src.startsWith('//')) src = 'https:' + src;
                        else if (src.startsWith('/')) src = base + src;
                        if (!images.includes(src)) {
                            images.push(src);
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
                        } else if (!language && (text.includes('function ') || text.includes('const ') || text.includes('let '))) {
                            language = 'javascript';
                        } else if (!language && (text.includes('def ') || text.includes('import '))) {
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

                return {
                    text: text,
                    html: html,
                    formats: formats,
                    codeBlocks: codeBlocks,
                    images: images,
                    embeddedContent: embeddedContent
                };
            }
        """, base_url)

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
        sorted_formats = sorted(content.get('formats', []), key=lambda x: len(x.get('text', '')), reverse=True)

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

        page = await self.context.new_page()

        try:
            # Set HTTP headers
            await page.set_extra_http_headers({
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8',
                'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
                'Accept-Encoding': 'gzip, deflate, br',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1',
                'Sec-Fetch-Dest': 'document',
                'Sec-Fetch-Mode': 'navigate',
                'Sec-Fetch-Site': 'none',
                'Cache-Control': 'max-age=0'
            })

            logger.info(f"Accessing tweet: {url}")

            # Navigate to page
            response = await page.goto(url, wait_until='domcontentloaded', timeout=90000)

            if response.status not in [200, 304]:
                logger.warning(f"Page returned status {response.status}")

            # Wait for tweet content to load
            try:
                await asyncio.wait_for(
                    asyncio.gather(
                        page.wait_for_selector('[data-testid="tweetText"]', timeout=15000),
                        return_exceptions=True
                    ),
                    timeout=15
                )
            except asyncio.TimeoutError:
                logger.warning("Timeout waiting for tweet elements, continuing anyway")

            # Expand long tweets
            logger.info("Checking for long tweet expansion...")
            await self.expand_long_tweet(page)
            await page.wait_for_timeout(1000)

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

            result = {
                'success': True,
                'title': title or 'X Post',
                'content': content.get('text', 'No content extracted'),
                'html': content.get('html', ''),
                'formats': content.get('formats', []),
                'codeBlocks': content.get('codeBlocks', []),
                'images': content.get('images', []),
                'url': url
            }

            logger.info(f"Successfully extracted tweet content ({len(result['content'])} chars, {len(result['images'])} images)")

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
                logger.error(f"Failed to scrape tweet: {result.get('error')}")
                return None

            # Determine content type based on media
            content_type = ContentType.TEXT
            html_content = result.get('html', '')
            if result.get('images'):
                content_type = ContentType.IMAGE
            # Check for video in HTML content
            if html_content and ('<video' in html_content or">video</span>" in html_content or
                                 'data-testid="videoPlayer"' in html_content):
                content_type = ContentType.VIDEO

            # Extract author from URL or title
            author_match = re.search(r'(twitter|x)\.com/(\w+)/status/', url)
            author = f"@{author_match.group(2)}" if author_match else "Unknown"

            return ContentInfo(
                url=url,
                title=result.get('title', 'Tweet'),
                description=result.get('content', '')[:200],
                content_type=content_type,
                uploader=author,
                upload_date=datetime.now().strftime('%Y-%m-%d'),
                metadata={
                    'tweet_id': tweet_id,
                    'images': result.get('images', []),
                    'formats': result.get('formats', []),
                    'full_content': result.get('content'),
                    'html': result.get('html')
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
        format_id: str | None = None
    ) -> DownloadResult:
        """Download content from Twitter/X"""
        try:
            logger.info(f"Downloading Twitter content: {url} ({content_type.value})")

            if progress_callback:
                await progress_callback({"status": "fetching", "progress": 20})

            result = await self.extractor.scrape_tweet(url)

            if not result.get('success'):
                return DownloadResult(
                    success=False,
                    error_message=result.get('error', 'Failed to scrape tweet')
                )

            if progress_callback:
                await progress_callback({"status": "processing", "progress": 50})

            temp_dir = tempfile.mkdtemp()
            images_dir = os.path.join(temp_dir, 'images')
            os.makedirs(images_dir, exist_ok=True)

            images = result.get('images', [])
            local_images = {}
            if images:
                if progress_callback:
                    await progress_callback({
                        "status": "downloading_images",
                        "progress": 60
                    })
                local_images = await self._download_images(images, images_dir)

            tweet_id = self.extract_tweet_id(url) or 'unknown'
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f"tweet_{tweet_id}_{timestamp}.html"
            temp_file = os.path.join(temp_dir, filename)

            html_content = self._generate_html(result, local_images)
            with open(temp_file, 'w', encoding='utf-8') as f:
                f.write(html_content)

            logger.info(f"Saved tweet content to temporary file: {temp_file}")

            if progress_callback:
                await progress_callback({"status": "completed", "progress": 100})

            author_match = re.search(r'(twitter|x)\.com/(\w+)/status/', url)
            author = f"@{author_match.group(2)}" if author_match else "Unknown"

            content_info = ContentInfo(
                url=url,
                title=result.get('title', 'Tweet'),
                description=result.get('content', '')[:200],
                content_type=ContentType.TEXT,
                uploader=author,
                upload_date=datetime.now().strftime('%Y-%m-%d'),
                metadata={
                    'images': list(local_images.values())
                }
            )

            return DownloadResult(
                success=True,
                file_path=temp_dir,
                content_info=content_info,
                error_message=None
            )

        except Exception as e:
            logger.error(f"Failed to download Twitter content: {e}")
            return DownloadResult(
                success=False,
                error_message=str(e)
            )

    def _generate_html(self, result: Dict[str, Any], local_images: Dict[str, str] = None) -> str:
        """Generate HTML file with original formatting preserved"""
        title = result.get('title', 'X Post')
        url = result.get('url', '')
        timestamp = datetime.now().strftime('%Y/%m/%d %H:%M:%S')
        content = result.get('content', '')
        html_content = result.get('html', '')
        local_images = local_images or {}

        html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            max-width: 800px;
            margin: 0 auto;
            padding: 20px;
            line-height: 1.8;
            background-color: #fff;
            color: #0f1419;
        }}
        .header {{
            border-bottom: 1px solid #e1e8ed;
            padding-bottom: 15px;
            margin-bottom: 20px;
        }}
        .header h1 {{
            font-size: 1.4em;
            margin: 0 0 10px 0;
            line-height: 1.3;
        }}
        .meta {{
            color: #536471;
            font-size: 0.9em;
        }}
        .meta a {{
            color: #1d9bf0;
            text-decoration: none;
        }}
        .content {{
            font-size: 1.05em;
        }}
        .content p {{
            margin: 0 0 1em 0;
        }}
        .content h2 {{
            font-size: 1.3em;
            font-weight: 600;
            margin: 1.5em 0 0.8em 0;
            color: #0f1419;
        }}
        .content h3 {{
            font-size: 1.15em;
            font-weight: 600;
            margin: 1.2em 0 0.6em 0;
            color: #0f1419;
        }}
        .content a {{
            color: #1d9bf0;
            text-decoration: none;
        }}
        .content strong {{
            font-weight: 600;
        }}
        .content em {{
            font-style: italic;
        }}
        .content img {{
            max-width: 100%;
            border-radius: 16px;
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
        .footer {{
            margin-top: 30px;
            padding-top: 15px;
            border-top: 1px solid #e1e8ed;
            color: #536471;
            font-size: 0.85em;
        }}
    </style>
</head>
<body>
    <div class="header">
        <h1>{title}</h1>
        <div class="meta">
            <p>原始链接: <a href="{url}" target="_blank">{url}</a></p>
            <p>提取时间: {timestamp}</p>
        </div>
    </div>
'''
        clean_content = self._clean_html_content(html_content, local_images) if html_content else content

        html += '    <div class="content">\n'
        html += clean_content + '\n'
        html += '    </div>\n'

        html += '''    <div class="footer">
        <p>由 YTBot 自动提取</p>
    </div>
</body>
</html>'''

        return html

    async def _download_images(self, image_urls: list, images_dir: str) -> Dict[str, str]:
        """Download images to local directory"""
        import aiohttp

        local_images = {}
        async with aiohttp.ClientSession() as session:
            for i, img_url in enumerate(image_urls):
                try:
                    async with session.get(img_url, timeout=aiohttp.ClientTimeout(total=30)) as response:
                        if response.status == 200:
                            content = await response.read()
                            ext = '.jpg'
                            if 'format=png' in img_url:
                                ext = '.png'
                            elif 'format=webp' in img_url:
                                ext = '.webp'
                            local_filename = f'image_{i+1}{ext}'
                            local_path = os.path.join(images_dir, local_filename)
                            with open(local_path, 'wb') as f:
                                f.write(content)
                            local_images[img_url] = f'images/{local_filename}'
                            logger.info(f"Downloaded image: {local_filename}")
                except Exception as e:
                    logger.warning(f"Failed to download image {img_url}: {e}")

        return local_images

    def _clean_html_content(self, html: str, local_images: Dict[str, str] = None) -> str:
        """Clean HTML content and extract meaningful text with formatting"""
        import re
        local_images = local_images or {}

        for orig_url, local_path in local_images.items():
            html = html.replace(orig_url, local_path)

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
        """Generate formatted Markdown from scraped tweet data"""
        title = result.get('title', 'X Post')
        url = result.get('url', '')
        timestamp = datetime.now().strftime('%Y/%m/%d %H:%M:%S')

        markdown = f"# {title}\n\n"
        markdown += f"> 原始链接: {url}\n"
        markdown += f"> 提取时间: {timestamp}\n\n"
        markdown += "---\n\n"

        images = result.get('images', [])
        if images:
            markdown += "## 图片\n\n"
            for img_url in images:
                markdown += f"![图片]({img_url})\n\n"
            markdown += "---\n\n"

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
