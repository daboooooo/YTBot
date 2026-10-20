"""Language/expansion helpers and scrape orchestration.

``ContentMixin`` groups original-language switching, long-tweet/thread
expansion, author/time extraction and the top-level ``scrape_tweet``
orchestration (single crash-recovery retry). Extracted verbatim from the
former monolithic ``platforms/twitter.py``.
"""

from datetime import datetime
from typing import Any, Dict, Optional

from ytbot.core.enhanced_logger import get_logger

logger = get_logger("ytbot.platforms.twitter")


class ContentMixin:
    """Scrape orchestration methods for TwitterContentExtractor (extracted)."""


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

    async def scrape_tweet(self, url: str) -> Dict[str, Any]:
        """Scrape a tweet, transparently recovering once from a browser
        crash (TargetClosedError). If the second attempt dies the same
        way, the failure result is returned for the caller/archiver to
        classify as a systemic browser error."""
        result = await self._scrape_tweet_once(url)
        if result.get("success"):
            return result
        error = result.get("error", "") or ""
        if self.BROWSER_CLOSED_HINT in error.lower():
            logger.warning(
                "Browser was closed mid-scrape; resetting browser and "
                "retrying once"
            )
            await self.reset_browser()
            return await self._scrape_tweet_once(url)
        return result

    async def _scrape_tweet_once(self, url: str) -> Dict[str, Any]:
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

        page = None

        try:
            page = await self.context.new_page()
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
                    if page is None:
                        return {
                            'success': False,
                            'error': (
                                '匿名访问也未获取到内容，'
                                'cookies 可能已失效，请更新 cookies'
                            ),
                            'url': url,
                        }
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
                if page is None:
                    return {
                        'success': False,
                        'error': (
                            '匿名访问也被拒绝 (403)，'
                            'cookies 可能已失效，请更新 cookies'
                        ),
                        'url': url,
                    }
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

            return result

        except Exception as e:
            logger.error(f"Failed to scrape tweet: {e}")
            return {
                'success': False,
                'error': str(e),
                'url': url
            }
        finally:
            if page is not None:
                try:
                    await page.close()
                except Exception:
                    pass
            # Resource lifecycle: shared contexts are created per scrape
            # and must be returned to the manager here (otherwise dozens
            # accumulate in one browser process); the one-off anonymous
            # browser is torn down completely.
            if self._ephemeral_browser:
                await self._dispose_standalone()
                self._ephemeral_browser = False
            elif self._using_shared_browser and self.context is not None:
                from ytbot.core.browser_manager import get_browser_manager
                mgr = await get_browser_manager()
                await mgr.close_context(self.context)
                self.context = None
                self._using_shared_browser = False
