"""Post detection and content extraction for Twitter/X scraping.

``PostsMixin`` groups post-type/thread/video detection, external-link
extraction and formatted/all-posts content extraction. Extracted
verbatim from the former monolithic ``platforms/twitter.py``.
"""

import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from ytbot.core.enhanced_logger import get_logger

logger = get_logger("ytbot.platforms.twitter")


class PostsMixin:
    """Post detection methods for TwitterContentExtractor (extracted)."""


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
                // 改进版：支持多种匹配方式，避免标题残留导致重复
                if (articleTitle && text && text.length > articleTitle.length) {
                    // 标准化：去除空格和标点进行匹配
                    function normalize(str) {
                        return str.replace(/[\\s\\p{P}\\p{S}]/gu, '').toLowerCase();
                    }
                    const normTitle = normalize(articleTitle);
                    const normText = normalize(text);

                    if (normText.includes(normTitle)) {
                        // 找到标准化后的位置，再映射回原始位置
                        const idx = normText.indexOf(normTitle);
                        if (idx >= 0) {
                            // 向前查找最近的换行或句号，避免只截断中间一段
                            let cleanStart = idx;
                            // 向前搜索不超过 100 字符
                            for (let i = idx; i >= Math.max(0, idx - 100); i--) {
                                if (
                                    text[i] === '\\n'
                                    || text[i] === '。'
                                    || text[i] === '.'
                                ) {
                                    cleanStart = i + 1;
                                    break;
                                }
                            }
                            text = text.substring(cleanStart).trim();
                        }
                    } else if (text.includes(articleTitle)) {
                        // 兜底：原始字符串匹配
                        const titleIdx = text.indexOf(articleTitle);
                        if (titleIdx >= 0 && titleIdx < 500) {
                            let cleanStart = titleIdx;
                            for (let i = titleIdx; i >= Math.max(0, titleIdx - 100); i--) {
                                if (
                                    text[i] === '\\n'
                                    || text[i] === '。'
                                    || text[i] === '.'
                                ) {
                                    cleanStart = i + 1;
                                    break;
                                }
                            }
                            text = text.substring(cleanStart).trim();
                        }
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
