"""
TwitterHandler PDF generation extension

This module provides PDF generation capabilities for Twitter/X content.
It should be integrated into the TwitterHandler class.
"""

import os
import tempfile
from pathlib import Path
from typing import Dict, Any, Optional, List
from datetime import datetime

from ..core.logger import get_logger
from ..services.pdf_converter import (
    pdf_converter,
    convert_html_to_pdf,
    is_pdf_conversion_available
)

logger = get_logger(__name__)


class TwitterPdfMixin:
    """
    Mixin class that adds PDF generation capabilities to TwitterHandler.

    This class should be inherited alongside the main TwitterHandler class.
    """

    async def generate_pdf_from_content(
        self,
        result: Dict[str, Any],
        local_images: Dict[str, Any],
        local_videos: List[str],
        output_dir: str,
        filename: Optional[str] = None
    ) -> Optional[str]:
        """
        Generate PDF from tweet content

        Args:
            result: Tweet content data
            local_images: Mapping of image URLs to local paths
            local_videos: List of local video paths
            output_dir: Output directory for PDF
            filename: Optional filename for the PDF

        Returns:
            PDF file path, or None if generation failed
        """
        if not is_pdf_conversion_available():
            logger.warning("PDF conversion not available, skipping PDF generation")
            return None

        try:
            html_content = self._generate_html(result, local_images, local_videos)

            if not html_content:
                logger.warning("Failed to generate HTML content")
                return None

            if filename is None:
                tweet_id = self.extract_tweet_id(result.get('url', '')) or 'unknown'
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                filename = f"tweet_{tweet_id}_{timestamp}.pdf"

            pdf_path = os.path.join(output_dir, filename)

            video_thumbnails = self._generate_video_thumbnails(local_videos)

            logger.info(f"Starting PDF generation for: {filename}")

            temp_html_fd, temp_html_path = tempfile.mkstemp(
                suffix='.html',
                prefix='ytbot_tweet_',
                dir=output_dir
            )

            try:
                with os.fdopen(temp_html_fd, 'w', encoding='utf-8') as f:
                    f.write(html_content)

                pdf_result = await convert_html_to_pdf(
                    temp_html_path,
                    pdf_path,
                    preprocess=True,
                    video_thumbnails=video_thumbnails
                )

                if pdf_result and os.path.exists(pdf_result):
                    file_size = os.path.getsize(pdf_result)
                    logger.info(
                        f"PDF generated successfully: {pdf_result} "
                        f"({file_size} bytes)"
                    )
                    return pdf_result
                else:
                    logger.warning(f"PDF generation failed: {pdf_result}")
                    return None

            finally:
                if os.path.exists(temp_html_path):
                    try:
                        os.remove(temp_html_path)
                    except Exception as e:
                        logger.debug(f"Failed to remove temp HTML: {e}")

        except Exception as e:
            logger.error(f"PDF generation error: {e}")
            return None

    def _generate_video_thumbnails(
        self,
        local_videos: List[str]
    ) -> Dict[str, str]:
        """
        Generate thumbnail paths for videos

        Args:
            local_videos: List of local video paths

        Returns:
            Mapping of video paths to thumbnail paths
        """
        thumbnails = {}

        for video_path in local_videos:
            if not video_path or not os.path.exists(video_path):
                continue

            video_path_obj = Path(video_path)

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

    async def download_and_generate_pdf(
        self,
        url: str,
        output_dir: str,
        progress_callback: Optional[Any] = None
    ) -> Optional[str]:
        """
        Download tweet and generate PDF directly

        Args:
            url: Tweet URL
            output_dir: Output directory for PDF
            progress_callback: Optional progress callback

        Returns:
            PDF file path, or None if failed
        """
        try:
            result = await self._extract_tweet_content(url, progress_callback)

            if not result or not result.get('success'):
                logger.error("Failed to extract tweet content")
                return None

            local_images = {}
            local_videos = []

            images_dir = os.path.join(output_dir, 'images')
            videos_dir = os.path.join(output_dir, 'videos')

            for img_url, img_info in result.get('images', {}).items():
                img_path = await self._download_image(img_url, images_dir)
                if img_path:
                    local_images[img_url] = {
                        'local_path': img_path,
                        'alt': img_info.get('alt', '')
                    }

            video_urls = result.get('video_urls', [])
            if video_urls:
                os.makedirs(videos_dir, exist_ok=True)

                valid_video_urls = [
                    v_url for v_url in video_urls
                    if v_url and not v_url.startswith('blob:')
                ]

                for video_url in valid_video_urls:
                    video_path = await self.download_video(video_url, videos_dir)
                    if video_path:
                        local_videos.append(video_path)

            pdf_path = await self.generate_pdf_from_content(
                result,
                local_images,
                local_videos,
                output_dir
            )

            return pdf_path

        except Exception as e:
            logger.error(f"PDF download and generation failed: {e}")
            return None
