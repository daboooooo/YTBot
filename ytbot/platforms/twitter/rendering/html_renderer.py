"""HTML rendering for scraped Twitter/X content.

Pure functions extracted verbatim from the former monolithic
``platforms/twitter.py`` (methods of ``TwitterHandler``). They use no
instance state; ``TwitterHandler`` keeps thin delegating wrappers during
the package split.
"""

import os
import re
from typing import Any, Dict, List, Optional  # noqa: F401

from ytbot.utils.common import format_iso_timestamp


def calculate_reading_time(text: str) -> int:
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


def add_heading_ids(html: str) -> tuple:
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


def generate_toc(headings: list) -> str:
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


def _is_video_thumbnail(url: str) -> bool:
    """Detect X/Twitter video poster thumbnails (not real photos).

    X serves native-video covers from
    ``pbs.twimg.com/amplify_video_thumb/...``. Such images are the poster
    of a video, so they must not be listed again in the image gallery
    (the video itself already has an entry in the media section).
    """
    if not url:
        return False
    lowered = url.lower()
    return 'amplify_video_thumb' in lowered or 'amplify_video' in lowered


def render_html(
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

    # Author's post time scraped from the tweet's <time datetime> (ISO
    # UTC). Never substitute the download time: when the timestamp could
    # not be scraped, show "未知" instead of fabricating the current time.
    publish_time = format_iso_timestamp(
        result.get('publish_time') or result.get('timestamp'),
        default='未知'
    )

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
        rt = calculate_reading_time(content)
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
        # 文章类型优先使用 html_content（完整渲染内容）
        clean_content = clean_html_content(
            html_content, local_images
        )
    elif content_parts and len(content_parts) > 1:
        # 多语言内容：分别渲染每个部分
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
                clean_part = clean_html_content(
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
            clean_content = clean_html_content(
                part_html, local_images
            )
        else:
            clean_content = f'<p>{part.get("text", content)}</p>'
    elif html_content:
        clean_content = clean_html_content(html_content, local_images)
    else:
        clean_content = f'<p>{content}</p>'

    # Generate TOC and add heading ids for articles
    toc_html = ''
    if is_article and clean_content:
        clean_content, headings = add_heading_ids(clean_content)
        toc_html = generate_toc(headings)
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

    # 收集主帖已展示的图片 URL，用于线程图片去重
    main_image_urls = set(result.get('images', []))

    if is_thread and thread_content_data:
        thread_posts = thread_content_data.get('thread_posts', [])
        if thread_posts:
            thread_html = '\n    <div class="thread-section">\n'
            thread_html += '        <h2>连续贴 (Thread)</h2>\n'
            thread_html += '        <div class="thread-posts">\n'

            for i, post in enumerate(thread_posts, 1):
                post_content = post.get('content', '')
                post_author = post.get('author', '')
                post_timestamp = format_iso_timestamp(
                    post.get('publish_time') or post.get('timestamp')
                )
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
                            clean_p = clean_html_content(
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

                # Add images for this thread post (去重：跳过主帖已展示的图片)
                if post_images:
                    displayed_in_thread = set()
                    thread_html += '                    <div class="thread-post-images">\n'
                    for j, img_url in enumerate(post_images, 1):
                        # 跳过主帖已展示的图片
                        if img_url in main_image_urls:
                            continue
                        # 跳过线程帖子内部重复的图片
                        if img_url in displayed_in_thread:
                            continue
                        displayed_in_thread.add(img_url)
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

    # Build media section HTML.
    #
    # Compute what will actually be rendered first, so an empty "媒体"
    # section is never emitted (e.g. an article whose images already
    # live in the content body).
    # Video poster thumbnails (amplify_video_thumb) are filtered out of
    # the gallery so they are not shown twice (once as an image, once as
    # the video that owns them).
    gallery_images = [
        url for url in images if not _is_video_thumbnail(url)
    ]
    if is_article:
        # Article images are already embedded in the content body —
        # don't duplicate them in the media section.
        gallery_images = []
    videos_to_show = local_videos if local_videos else video_urls

    media_html = ''
    has_media = gallery_images or videos_to_show or embedded_videos
    if has_media:
        media_html = '\n    <div class="media-section">\n'
        media_html += '        <h2>媒体</h2>\n'

        if gallery_images:
            media_html += '        <div class="media-group">\n'
            media_html += (
                '            <h3 class="media-subtitle">🖼️ 图片'
                f'<span class="media-count">{len(gallery_images)}</span>'
                '</h3>\n'
            )
            media_html += '            <div class="images-grid">\n'
            for i, img_url in enumerate(gallery_images, 1):
                # Use local path if available, otherwise original URL
                if img_url in local_images:
                    img_info = local_images[img_url]
                    img_path = img_info.get('local_path', img_url)
                    alt_text = img_info.get('alt', f'图片 {i}')
                else:
                    img_path = img_url
                    alt_text = f'图片 {i}'
                media_html += (
                    f'                <div class="image-item">\n'
                    f'                    <img src="{img_path}" '
                    f'alt="{alt_text}" loading="lazy">\n'
                    f'                    <span class="image-label">'
                    f'图片 {i}</span>\n'
                    f'                </div>\n'
                )
            media_html += '            </div>\n'
            media_html += '        </div>\n'

        # Native videos with player
        # (videos_to_show falls back to original URLs, see above)
        if videos_to_show:
            media_html += '        <div class="media-group">\n'
            media_html += (
                '            <h3 class="media-subtitle">🎬 视频'
                f'<span class="media-count">{len(videos_to_show)}</span>'
                '</h3>\n'
            )
            media_html += '            <div class="videos-section">\n'
            for i, video_path in enumerate(videos_to_show, 1):
                # Get just the filename for display
                if '/' in video_path:
                    video_filename = os.path.basename(video_path)
                else:
                    video_filename = video_path
                media_html += (
                    f'                <div class="video-item">\n'
                    f'                    <span class="video-label">'
                    f'视频 {i}</span>\n'
                    f'                    <video controls preload="metadata">\n'
                    f'                        <source '
                    f'src="videos/{video_filename}" type="video/mp4">\n'
                    f'                        您的浏览器不支持视频播放。\n'
                    f'                    </video>\n'
                    f'                </div>\n'
                )
            media_html += '            </div>\n'
            media_html += '        </div>\n'

        # Embedded videos (YouTube, Vimeo, etc.)
        if embedded_videos:
            media_html += '        <div class="media-group">\n'
            media_html += (
                '            <h3 class="media-subtitle">🔗 嵌入视频'
                f'<span class="media-count">{len(embedded_videos)}</span>'
                '</h3>\n'
            )
            media_html += '            <div class="embedded-videos-section">\n'
            for video in embedded_videos:
                platform = video.get('type', 'video')
                video_url = video.get('url', '')
                video_title = video.get('title', '')
                video_id = video.get('videoId', '')

                media_html += (
                    '                <div class="embedded-video-item">\n'
                    f'                    <span class="video-label">'
                    f'嵌入视频 ({platform})</span>\n'
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

                media_html += '                </div>\n'
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
        line-height: 1.7;
    }}
    .content h2 {{
        font-size: 1.3em;
        font-weight: 700;
        margin: 2em 0 1em 0;
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
    .content img, .content figure {{
        max-width: 100%;
        border-radius: 12px;
        margin: 1.5em 0;
        display: block;
    }}
    .content pre {{
        background: #1e1e1e;
        color: #d4d4d4;
        padding: 16px;
        border-radius: 8px;
        overflow-x: auto;
        margin: 1.5em 0;
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
    .media-group {{
        margin-bottom: 8px;
    }}
    .media-group + .media-group {{
        margin-top: 24px;
        padding-top: 20px;
        border-top: 1px dashed #e1e8ed;
    }}
    .media-subtitle {{
        display: flex;
        align-items: center;
        gap: 8px;
        font-size: 1em;
        font-weight: 600;
        color: #0f1419;
        margin: 0 0 14px 0;
    }}
    .media-count {{
        display: inline-block;
        min-width: 20px;
        padding: 1px 8px;
        border-radius: 10px;
        background: #e8f5fe;
        color: #1d9bf0;
        font-size: 0.8em;
        font-weight: 600;
        text-align: center;
    }}
    .images-grid {{
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
        gap: 12px;
    }}
    .image-item {{
        position: relative;
        border-radius: 12px;
        overflow: hidden;
        background: #f7f9fa;
    }}
    .image-item img {{
        width: 100%;
        height: auto;
        display: block;
        border-radius: 12px;
        object-fit: cover;
    }}
    .image-label {{
        display: block;
        margin-top: 6px;
        font-size: 0.8em;
        color: #536471;
        text-align: center;
    }}
    .videos-section, .embedded-videos-section {{
        display: flex;
        flex-direction: column;
        gap: 20px;
    }}
    .video-item, .embedded-video-item {{
        margin: 0;
    }}
    .video-label {{
        display: inline-block;
        font-size: 0.85em;
        font-weight: 600;
        color: #536471;
        margin-bottom: 8px;
    }}
    .video-item video {{
        width: 100%;
        max-height: 500px;
        border-radius: 12px;
        display: block;
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
        .media-group + .media-group {{
            border-top: 1px solid #ccc;
        }}
        .media-subtitle {{
            color: #000;
        }}
        .media-count {{
            background: #eee;
            color: #000;
        }}
        .images-grid {{
            grid-template-columns: 1fr;
            gap: 12px;
        }}
        .image-item {{
            border-radius: 0;
            background: #fff;
        }}
        .image-item img {{
            border-radius: 0;
            page-break-inside: avoid;
        }}
        .videos-section, .embedded-videos-section {{
            display: block;
            page-break-inside: avoid;
        }}
        .video-item, .embedded-video-item {{
            margin-bottom: 16px;
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


def clean_html_content(html: str, local_images: Dict[str, Any] = None) -> str:
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

    html = convert_x_code_blocks_to_pre(html)

    html = re.sub(r'<section[^>]*>', '', html)
    html = re.sub(r'</section>', '\n\n', html)
    # 改进 div 处理：移除嵌套 div，保留内部内容
    # 先处理嵌套 div，逐层剥离
    for _ in range(5):  # 最多迭代 5 次处理深层嵌套
        prev_html = html
        # 移除无属性的 div 对
        html = re.sub(r'<div>\s*', '', html)
        html = re.sub(r'\s*</div>', '', html)
        # 移除有属性的 div，替换为换行
        html = re.sub(r'<div[^>]*>', '', html)
        html = re.sub(r'</div>', '\n', html)
        if html == prev_html:
            break
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

    html = preserve_pre_blocks(html, lambda h: re.sub(r'<br\s*/?>', '\n', h))
    html = preserve_pre_blocks(html, lambda h: re.sub(r'<p[^>]*>', '\n\n', h))
    html = preserve_pre_blocks(html, lambda h: re.sub(r'</p>', '\n\n', h))

    html = re.sub(r'<img[^>]*>', r'\n\n\g<0>\n\n', html)

    html = re.sub(r'\n\s*\n\s*\n+', '\n\n', html)
    # 保护pre块后移除行首空白，避免代码缩进丢失
    html = preserve_pre_blocks(
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
    # 合并连续空行，但保留标题/代码块周围的间隔
    result = re.sub(r'\n\s*\n\s*\n+', '\n\n', result)
    # 移除空段落标签
    result = re.sub(r'<p>\s*</p>', '', result)
    return result.strip()


def convert_x_code_blocks_to_pre(html_content: str) -> str:
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


def preserve_pre_blocks(html_content: str, transform_func) -> str:
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
