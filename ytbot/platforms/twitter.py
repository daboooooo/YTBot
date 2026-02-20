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
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    logger.warning(
        "Playwright not installed. Twitter/X content extraction will not work. "
        "Install with: pip install playwright && playwright install chromium"
    )


class TwitterContentExtractor:
    """
    Extracts content from Twitter/X using Playwright to bypass anti-bot protection
    """

    def __init__(self):
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None

    async def initialize_browser(self):
        """Initialize Playwright browser with anti-detection measures"""
        if not PLAYWRIGHT_AVAILABLE:
            raise RuntimeError("Playwright not installed. Install with: pip install playwright && playwright install chromium")

        if self.browser is None:
            playwright = await async_playwright().start()

            # Launch browser with anti-detection settings
            self.browser = await playwright.chromium.launch(
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
        """Close the browser instance"""
        if self.browser:
            await self.browser.close()
            self.browser = None
            self.context = None

    async def expand_long_tweet(self, page) -> bool:
        """Click 'Show more' button to expand long tweets"""
        expand_selectors = [
            'div[role="button"]:has-text("显示更多")',
            'div[role="button"]:has-text("Show more")',
            '[data-testid="tweet-text-show-more-link"]',
            'span:has-text("显示更多")',
            'span:has-text("Show more")'
        ]

        for selector in expand_selectors:
            try:
                btn = await page.query_selector(selector)
                if btn:
                    await btn.click()
                    await page.wait_for_timeout(1500)
                    logger.info("Expanded long tweet content")
                    return True
            except Exception:
                continue

        return False

    async def extract_formatted_content(self, page, base_url: str) -> Dict[str, Any]:
        """Extract tweet content with formatting information"""
        return await page.evaluate("""
            (base) => {
                const tweetElement = document.querySelector('[data-testid="tweetText"]') ||
                                     document.querySelector('article [lang]') ||
                                     document.querySelector('article');

                if (!tweetElement) return { text: '', html: '', formats: [], images: [] };

                // Extract images
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

                // Extract code blocks
                const codeBlocks = [];
                tweetElement.querySelectorAll('code, pre').forEach(c => {
                    const text = c.textContent;
                    if (text.includes('\\n') || text.length > 50) {
                        codeBlocks.push({
                            text: text,
                            isMultiline: true
                        });
                    }
                });

                // Extract formatting information
                const formats = [];

                // Links
                tweetElement.querySelectorAll('a').forEach(a => {
                    let href = a.getAttribute('href');
                    const text = a.textContent.trim();
                    if (href && !href.includes('twitter.com/intent') && text) {
                        if (href.startsWith('/')) {
                            href = base + href;
                        }
                        // Filter out analytics, view counts, etc.
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

                // Bold text
                tweetElement.querySelectorAll('strong, b').forEach(b => {
                    const text = b.textContent.trim();
                    if (text && text.length > 1) {
                        formats.push({ type: 'bold', text: text });
                    }
                });

                // Inline code
                tweetElement.querySelectorAll('code').forEach(c => {
                    const text = c.textContent.trim();
                    if (text && !c.closest('pre')) {
                        const isMultiLine = text.includes('\\n') || text.length > 50;
                        if (!isMultiLine) {
                            formats.push({ type: 'code', text: text });
                        }
                    }
                });

                // Italic text
                tweetElement.querySelectorAll('em, i').forEach(i => {
                    const text = i.textContent.trim();
                    if (text && text.length > 1 && !i.closest('a') && !i.closest('code')) {
                        formats.push({ type: 'italic', text: text });
                    }
                });

                // Clean text content
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

                // Clean HTML
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
                    images: images
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
                if markdown.includes(full_text):
                    markdown = markdown.replace(full_text, placeholder)
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
                            markdown = markdown[:start_idx] + placeholder + markdown[end_idx:]

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
            if result.get('images'):
                content_type = ContentType.IMAGE

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

        html = re.sub(r'<section[^>]*>', '', html)
        html = re.sub(r'</section>', '\n\n', html)
        html = re.sub(r'<div[^>]*>', '', html)
        html = re.sub(r'</div>', '\n', html)
        html = re.sub(r'<span[^>]*>', '', html)
        html = re.sub(r'</span>', '', html)
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

        html = re.sub(r'<br\s*/?>', '\n', html)
        html = re.sub(r'<p[^>]*>', '\n\n', html)
        html = re.sub(r'</p>', '\n\n', html)

        html = re.sub(r'<img[^>]*>', r'\n\n\g<0>\n\n', html)

        html = re.sub(r'\n\s*\n\s*\n+', '\n\n', html)
        html = re.sub(r'^\s+', '', html, flags=re.MULTILINE)

        lines = html.split('\n')
        formatted_lines = []
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue

            if stripped.startswith('<h2>') or stripped.startswith('<h3>'):
                formatted_lines.append('')
                formatted_lines.append(stripped)
                formatted_lines.append('')
            elif stripped.startswith('<img'):
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
