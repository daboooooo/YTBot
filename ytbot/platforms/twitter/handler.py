"""Twitter/X platform handler.

Platform-interface implementation (URL matching, content info, download
orchestration). Rendering lives in ``rendering/``, media downloads in
``media.py`` and scraping in ``extractor/``.
"""

import asyncio
import os
import re
import shutil
import tempfile
from datetime import datetime
from typing import Any, Dict, List, Optional

from ytbot.core.enhanced_logger import get_logger
from ytbot.platforms.base import (
    ContentInfo,
    ContentType,
    DownloadResult,
    PlatformHandler,
)
from ytbot.services.pdf_converter import pdf_converter
from ytbot.services.storage_service import get_storage_service
from ytbot.utils.system_proxy import get_system_proxy

from .extractor import TwitterContentExtractor
from .media import (
    download_images,
    download_video,
    generate_video_thumbnails_mapping,
)
from .rendering.html_renderer import render_html

# Try to import aiohttp for link preview
aiohttp = None
try:
    import aiohttp
except ImportError:
    pass

logger = get_logger("ytbot.platforms.twitter")


def _iso_to_date(raw: Optional[str]) -> Optional[str]:
    """Convert an ISO-8601 tweet timestamp (``time[datetime]``, UTC) to a
    ``YYYY-MM-DD`` post date. Returns ``None`` when the value is missing or
    not ISO-parseable (e.g. relative DOM text) — never the download date.
    """
    if not raw:
        return None
    try:
        return datetime.fromisoformat(
            raw.replace('Z', '+00:00')
        ).strftime('%Y-%m-%d')
    except (ValueError, TypeError):
        return None


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
        self.storage_service = get_storage_service()

    def _parse_twitter_error(self, error_msg: str) -> str:
        error_lower = error_msg.lower()
        if 'target page, context or browser has been closed' in error_lower:
            return "ERROR_BROWSER_CLOSED"
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
            proxy = get_system_proxy(url)
            async with session.get(url, timeout=timeout, proxy=proxy, headers={
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
        article_title: str = "",
        timestamp: str = ""
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
            timestamp: The post's own ISO-8601 timestamp (UTC); used in the
                empty-content title fallback, never the download time

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
            # Empty content, use {@author} - {post date}; when the post
            # time is unknown the author alone is used (never download date)
            date_str = _iso_to_date(timestamp)
            title = f"{author} - {date_str}" if date_str else str(author)

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
                article_title=article_title,
                timestamp=result.get('timestamp', '')
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
                upload_date=_iso_to_date(
                    result.get('publish_time') or result.get('timestamp')
                ),
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
        pre_scraped_result: Optional[Dict[str, Any]] = None,
        archive_mode: bool = False
    ) -> DownloadResult:
        """Download content from Twitter/X"""
        try:
            logger.info(
                "Downloading Twitter content: %s (%s)",
                url,
                content_type.value
            )

            if progress_callback:
                progress_callback({"status": "fetching", "progress": 20})

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
                progress_callback({"status": "processing", "progress": 50})

            temp_dir = tempfile.mkdtemp()
            images_dir = os.path.join(temp_dir, 'images')
            os.makedirs(images_dir, exist_ok=True)

            # Download images
            images = result.get('images', [])
            local_images = {}
            if images:
                if progress_callback:
                    progress_callback({
                        "status": "downloading_images",
                        "progress": 60
                    })
                local_images = await download_images(images, images_dir)

            # Download videos if present
            video_urls = result.get('video_urls', [])
            local_videos = []
            has_video = result.get('has_video', False)
            # Download videos if detected (regardless of content_type or post_type)
            # yt-dlp can extract videos from tweet URLs even for article-type posts
            if has_video and not archive_mode:
                if progress_callback:
                    progress_callback({
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
                        video_path = await download_video(
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
                    video_path = await download_video(url, videos_dir)
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

            html_content = render_html(result, local_images, local_videos)
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
                        generate_video_thumbnails_mapping(local_videos)
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
                progress_callback({"status": "completed", "progress": 100})

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
                article_title=article_title,
                timestamp=result.get('timestamp', '')
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
                upload_date=_iso_to_date(
                    result.get('publish_time') or result.get('timestamp')
                ),
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
