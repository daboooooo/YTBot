"""
PDF generation preprocessor for YTBot

Responsible for converting HTML content to PDF-friendly format:
1. Path processing (relative paths → absolute paths)
2. Video processing (replace with thumbnail placeholders)
3. Iframe processing (extract cover images and links)
4. Add print-optimized CSS styles
"""

import html
import re
import os
from pathlib import Path
from typing import Dict, Optional
from ..core.enhanced_logger import get_logger

logger = get_logger(__name__)


class PdfPreprocessor:
    """PDF preprocessor for preparing HTML content"""

    def __init__(self):
        self.placeholder_image = None

    def preprocess(
        self,
        html_content: str,
        html_file_dir: str,
        video_thumbnails: Optional[Dict[str, str]] = None
    ) -> str:
        """
        Preprocess HTML for PDF conversion

        Args:
            html_content: Original HTML content
            html_file_dir: HTML file directory (for resolving relative paths)
            video_thumbnails: Mapping of video files to thumbnails

        Returns:
            Preprocessed HTML content
        """
        pdf_html = html_content

        pdf_html = self._convert_relative_paths(pdf_html, html_file_dir)

        pdf_html = self._process_local_videos(
            pdf_html,
            html_file_dir,
            video_thumbnails or {}
        )

        pdf_html = self._process_iframe_videos(pdf_html)

        pdf_html = self._add_print_styles(pdf_html)

        return pdf_html

    def _convert_relative_paths(
        self,
        html: str,
        html_dir: str
    ) -> str:
        """Convert relative paths to absolute paths"""

        def make_absolute(match):
            attr = match.group(1)
            value = match.group(2)

            if value.startswith(('http://', 'https://', 'file://', '/')):
                return f'{attr}="{value}"'

            abs_path = os.path.abspath(os.path.join(html_dir, value))
            return f'{attr}="{abs_path}"'

        html = re.sub(
            r'(src|href)="([^"]+)"',
            make_absolute,
            html
        )

        return html

    def _process_local_videos(
        self,
        html: str,
        html_dir: str,
        thumbnails: Dict[str, str]
    ) -> str:
        """Process local videos, replace with thumbnail placeholders"""

        def replace_video(match):
            video_tag = match.group(0)

            src_match = re.search(r'<source[^>]+src="([^"]+)"', video_tag)
            if not src_match:
                src_match = re.search(r'src="([^"]+)"', video_tag)

            if src_match:
                video_src = src_match.group(1)

                if not video_src.startswith('/'):
                    video_src = os.path.abspath(os.path.join(html_dir, video_src))

                thumbnail = thumbnails.get(video_src)
                if not thumbnail:
                    thumbnail = self._generate_thumbnail_path(video_src)

                video_title = os.path.basename(video_src)

                return self._generate_video_placeholder(
                    thumbnail=thumbnail,
                    video_title=video_title,
                    video_src=video_src
                )

            return '<div class="pdf-placeholder">[Video Content]</div>'

        html = re.sub(
            r'<video[^>]*>.*?</video>',
            replace_video,
            html,
            flags=re.DOTALL
        )

        return html

    def _process_iframe_videos(self, html: str) -> str:
        """Process iframe embedded videos (YouTube, Vimeo, etc.)"""

        def replace_iframe(match):
            iframe_tag = match.group(0)

            src_match = re.search(r'src="([^"]+)"', iframe_tag)
            if not src_match:
                return iframe_tag

            src = src_match.group(1)
            title_match = re.search(r'title="([^"]+)"', iframe_tag)
            title = title_match.group(1) if title_match else "Embedded Video"

            yt_match = re.search(r'youtube\.com/embed/([^?&"]+)', src)
            if yt_match:
                video_id = yt_match.group(1)
                thumbnail = f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg"
                video_url = f"https://youtu.be/{video_id}"

                return self._generate_youtube_placeholder(
                    thumbnail=thumbnail,
                    title=title,
                    video_url=video_url,
                    video_id=video_id
                )

            vimeo_match = re.search(r'vimeo\.com/video/(\d+)', src)
            if vimeo_match:
                video_id = vimeo_match.group(1)
                video_url = f"https://vimeo.com/{video_id}"

                escaped_title = html.escape(title)
                escaped_url = html.escape(video_url)

                return '''
                <div class="pdf-embedded-video">
                    <div class="video-placeholder">
                        <span style="font-size: 48px;">▶️</span>
                        <p>{title}</p>
                        <a href="{url}">{url}</a>
                    </div>
                </div>
                '''.format(title=escaped_title, url=escaped_url)

            escaped_src = html.escape(src)
            escaped_title = html.escape(title)

            return '<p>[Embedded Content: <a href="{src}">{title}</a>]</p>'.format(
                src=escaped_src,
                title=escaped_title
            )

        html = re.sub(
            r'<iframe[^>]*>.*?</iframe>',
            replace_iframe,
            html,
            flags=re.DOTALL
        )

        return html

    def _generate_thumbnail_path(self, video_path: str) -> Optional[str]:
        """Generate thumbnail path for video"""

        video_path_obj = Path(video_path)

        possible_thumbnails = [
            video_path_obj.with_suffix('.jpg'),
            video_path_obj.with_suffix('.png'),
            video_path_obj.with_suffix('.thumb.jpg'),
            video_path_obj.parent / 'thumbnails' / f'{video_path_obj.stem}.jpg',
        ]

        for thumb in possible_thumbnails:
            if thumb.exists():
                return str(thumb)

        return None

    def _generate_video_placeholder(
        self,
        thumbnail: Optional[str],
        video_title: str,
        video_src: str
    ) -> str:
        """Generate video placeholder HTML"""

        if thumbnail and os.path.exists(thumbnail):
            img_tag = '<img src="{thumb}" alt="Video Thumbnail" style="width: 100%;">'.format(
                thumb=html.escape(thumbnail)
            )
        else:
            img_tag = (
                '<div style="height: 200px; background: #333; '
                'display: flex; align-items: center; '
                'justify-content: center; color: white;">'
                '🎬</div>'
            )

        escaped_title = html.escape(video_title)

        return '''
        <div class="pdf-video-placeholder" style="
            border: 2px solid #ddd;
            border-radius: 8px;
            overflow: hidden;
            margin: 16px 0;
            background: #f9f9f9;
        ">
            {img_tag}
            <div style="padding: 12px; text-align: center;">
                <p style="margin: 0 0 8px 0; font-weight: bold;">🎬 {title}</p>
                <p style="margin: 0; color: #666; font-size: 14px;">
                    Video content (view full video in HTML version)
                </p>
            </div>
        </div>
        '''.format(img_tag=img_tag, title=escaped_title)

    def _generate_youtube_placeholder(
        self,
        thumbnail: str,
        title: str,
        video_url: str,
        video_id: str
    ) -> str:
        """Generate YouTube video placeholder HTML"""

        escaped_thumbnail = html.escape(thumbnail)
        escaped_title = html.escape(title)
        escaped_url = html.escape(video_url)

        return '''
        <div class="pdf-youtube-placeholder" style="
            border: 2px solid #ddd;
            border-radius: 8px;
            overflow: hidden;
            margin: 16px 0;
            background: #f9f9f9;
        ">
            <img src="{thumb}" alt="{title}" style="width: 100%; opacity: 0.9;">
            <div style="padding: 12px; text-align: center;">
                <p style="margin: 0 0 8px 0; font-weight: bold;">▶️ {title}</p>
                <p style="margin: 0; color: #1da1f2; font-size: 14px;">
                    <a href="{url}">{url}</a>
                </p>
                <p style="margin: 8px 0 0 0; color: #666; font-size: 12px;">
                    YouTube video (watch in HTML version)
                </p>
            </div>
        </div>
        '''.format(thumb=escaped_thumbnail, title=escaped_title, url=escaped_url)

    def _add_print_styles(self, html: str) -> str:
        """Add print-optimized styles"""

        print_css = '''
        <style>
            @media print {
                @page {
                    size: A4;
                    margin: 2cm;
                }

                body {
                    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                    font-size: 12pt;
                    line-height: 1.6;
                    color: #333;
                }

                img {
                    max-width: 100% !important;
                    page-break-inside: avoid;
                }

                .pdf-video-placeholder,
                .pdf-youtube-placeholder {
                    page-break-inside: avoid;
                    border: 1px solid #ccc;
                    margin: 16px 0;
                }

                a {
                    color: #333;
                    text-decoration: none;
                }

                a[href^="http"]:after {
                    content: " (" attr(href) ")";
                    font-size: 0.8em;
                    color: #666;
                }

                .header {
                    border-bottom: 2px solid #333;
                    padding-bottom: 16px;
                    margin-bottom: 24px;
                }

                .media-section,
                .thread-section {
                    margin-top: 24px;
                }

                h1, h2, h3 {
                    page-break-after: avoid;
                }
            }
        </style>
        '''

        return html.replace('</head>', f'{print_css}</head>')


pdf_preprocessor = PdfPreprocessor()


def preprocess_for_pdf(
    html_content: str,
    html_file_dir: str,
    video_thumbnails: Optional[Dict[str, str]] = None
) -> str:
    """Convenience function to preprocess HTML for PDF conversion"""
    return pdf_preprocessor.preprocess(
        html_content,
        html_file_dir,
        video_thumbnails
    )
