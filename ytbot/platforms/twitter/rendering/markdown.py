"""Markdown rendering for scraped Twitter/X content.

Pure functions extracted verbatim from the former monolithic
``platforms/twitter.py`` (methods of ``TwitterContentExtractor`` and
``TwitterHandler``). No instance state is used; both classes keep thin
delegating wrappers during the package split.
"""

import re  # noqa: F401
from typing import Any, Dict

from ytbot.utils.common import format_iso_timestamp


def convert_to_markdown(content: Dict[str, Any]) -> str:
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
            markdown = replace_code_in_markdown(
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

def replace_code_in_markdown(markdown: str, full_text: str, placeholder: str) -> str:
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

def generate_markdown(result: Dict[str, Any]) -> str:
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

    # Author's post time from the scraped <time datetime> (ISO UTC);
    # never substitute the download time.
    publish_time = format_iso_timestamp(
        result.get('publish_time') or result.get('timestamp'),
        default='未知'
    )

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
        formatted_content = html_to_markdown(html_content)
    else:
        content = result.get('content', '')
        if content:
            formatted_content = convert_to_markdown({
                'text': content,
                'formats': result.get('formats', []),
                'codeBlocks': result.get('codeBlocks', [])
            })
            formatted_content = format_paragraphs(formatted_content)
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

def html_to_markdown(html: str) -> str:
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
    text = format_paragraphs(text)
    return text

def format_paragraphs(text: str) -> str:
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

        if is_list_item(stripped):
            formatted_lines.append(f"- {stripped.lstrip('- •·').strip()}")
            prev_was_empty = False
        else:
            formatted_lines.append(stripped)
            prev_was_empty = False

    return '\n'.join(formatted_lines)

def is_section_header(line: str) -> bool:
    """Check if line looks like a section header"""
    return False

def is_list_item(line: str) -> bool:
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
