"""Media downloading for Twitter/X content.

Functions extracted verbatim from the former monolithic
``platforms/twitter.py`` (methods of ``TwitterHandler``): image extension
detection, concurrent image downloads, and native video downloads via
yt-dlp. ``TwitterHandler`` keeps thin delegating wrappers.
"""

import asyncio
import os
import re  # noqa: F401
from datetime import datetime
from typing import Any, Dict, List, Optional

from ytbot.core.enhanced_logger import get_logger
from ytbot.utils.system_proxy import get_system_proxy

# Try to import aiohttp (optional dependency, mirrors the legacy module)
aiohttp = None
try:
    import aiohttp
except ImportError:
    pass

logger = get_logger("ytbot.platforms.twitter")


def generate_video_thumbnails_mapping(
    local_videos: List[str]
) -> Dict[str, str]:
    """Generate thumbnail paths for videos"""
    from pathlib import Path as PathLib

    thumbnails = {}

    for video_path in local_videos:
        if not video_path or not os.path.exists(video_path):
            continue

        video_path_obj = PathLib(video_path)

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

def detect_image_extension(img_url: str, content_type: str = None) -> str:
    """
    Detect image extension from URL and content type.

    Supports: webp, png, jpg, jpeg, gif

    Args:
        img_url: The image URL
        content_type: HTTP Content-Type header
            (optional)

    Returns:
        File extension including the dot (e.g., '.jpg')
    """
    # Check URL patterns first
    url_lower = img_url.lower()

    # Check for format parameter in URL
    if 'format=webp' in url_lower or 'format=web' in url_lower:
        return '.webp'
    if 'format=png' in url_lower:
        return '.png'
    if 'format=jpg' in url_lower or 'format=jpeg' in url_lower:
        return '.jpg'
    if 'format=gif' in url_lower:
        return '.gif'

    # Check file extension in URL path
    import re
    ext_match = re.search(r'\.(webp|png|jpe?g|gif)(?:\?|#|$)', url_lower)
    if ext_match:
        ext = ext_match.group(1)
        if ext == 'jpeg':
            return '.jpg'
        return f'.{ext}'

    # Check content type if available
    if content_type:
        ct_lower = content_type.lower()
        if 'webp' in ct_lower:
            return '.webp'
        if 'png' in ct_lower:
            return '.png'
        if 'gif' in ct_lower:
            return '.gif'
        if 'jpeg' in ct_lower or 'jpg' in ct_lower:
            return '.jpg'

    # Default to jpg
    return '.jpg'

async def download_images(
    image_urls: list,
    images_dir: str,
    image_metadata: List[Dict] = None
) -> Dict[str, Dict[str, Any]]:
    """
    Download images to local directory with metadata.
    Uses parallel download with concurrency limit to avoid rate limiting.

    Args:
        image_urls: List of image URLs to download
        images_dir: Directory to save images
        image_metadata: Optional list of image metadata dicts

    Returns:
        Dict mapping original URL to dict with:
            - local_path: Relative path to saved image
            - filename: Saved filename
            - size: File size in bytes
            - width: Image width (if available)
            - height: Image height (if available)
            - format: Image format
    """
    from ytbot.utils.async_utils import gather_with_concurrency

    local_images = {}
    metadata_map = {}
    if image_metadata:
        for meta in image_metadata:
            url = meta.get('url')
            if url:
                metadata_map[url] = meta

    if not image_urls:
        return local_images

    async def download_single_image(img_url: str, index: int) -> Dict[str, Any]:
        try:
            timeout = aiohttp.ClientTimeout(total=30)
            proxy = get_system_proxy(img_url)
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    img_url, timeout=timeout, proxy=proxy
                ) as response:
                    if response.status == 200:
                        content = await response.read()
                        content_type = response.headers.get(
                            'Content-Type', ''
                        )

                        ext = detect_image_extension(
                            img_url, content_type
                        )

                        local_filename = f'image_{index + 1}{ext}'
                        local_path = os.path.join(
                            images_dir, local_filename
                        )

                        with open(local_path, 'wb') as f:
                            f.write(content)

                        file_size = len(content)
                        meta = metadata_map.get(img_url, {})

                        result = {
                            img_url: {
                                'local_path': (
                                    f'images/{local_filename}'
                                ),
                                'filename': local_filename,
                                'size': file_size,
                                'width': meta.get('width'),
                                'height': meta.get('height'),
                                'format': ext.lstrip('.'),
                                'alt': meta.get('alt')
                            }
                        }

                        logger.info(
                            f"Downloaded image "
                            f"{index + 1}/{len(image_urls)}: "
                            f"{local_filename} ({file_size} bytes)"
                        )
                        return result
                    else:
                        logger.warning(
                            f"Image download failed with "
                            f"status {response.status}: {img_url}"
                        )
        except Exception as e:
            logger.warning(f"Failed to download image {img_url}: {e}")
        return {}

    results = await gather_with_concurrency(
        3,
        *[download_single_image(url, i) for i, url in enumerate(image_urls)]
    )

    for result in results:
        local_images.update(result)

    return local_images

async def download_video(video_url: str, output_path: str) -> Optional[str]:
    """
    Download X/Twitter native video using yt-dlp.

    Args:
        video_url: The video URL to download
        output_path: Directory to save the video

    Returns:
        Path to the downloaded video file, or None if download failed
    """
    try:
        # Check if yt-dlp is available
        try:
            import yt_dlp
        except ImportError:
            logger.error(
                "yt-dlp not installed. Cannot download videos. "
                "Install with: pip install yt-dlp"
            )
            return None

        # Ensure output directory exists
        os.makedirs(output_path, exist_ok=True)

        # Generate output filename
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        output_template = os.path.join(
            output_path, f'video_{timestamp}.%(ext)s'
        )

        # Configure yt-dlp options
        ydl_opts = {
            'format': 'best',  # Download best quality
            'outtmpl': output_template,
            'quiet': True,
            'no_warnings': True,
            'cookiesfrombrowser': None,  # Don't use browser cookies
            # Proxied links can drop mid-transfer; retry and resume
            # instead of leaving a .part file after a few megabytes.
            'retries': 5,
            'fragment_retries': 10,
            'continuedl': True,
            'http_chunk_size': 1048576,
            'http_headers': {
                'User-Agent': (
                    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                    'AppleWebKit/537.36 (KHTML, like Gecko) '
                    'Chrome/120.0.0.0 Safari/537.36'
                ),
                'Accept': '*/*',
                'Accept-Language': 'en-US,en;q=0.9',
            },
        }

        # yt-dlp does not read the macOS/Windows system proxy on its
        # own; pass the resolved proxy explicitly (None = direct).
        proxy = get_system_proxy(video_url)
        if proxy:
            ydl_opts['proxy'] = proxy
            logger.info(f"Using proxy for video download: {proxy}")

        # Skip blob URLs (browser-local, not accessible by yt-dlp)
        if video_url.startswith('blob:'):
            logger.warning(f"Skipping blob URL (not accessible): {video_url}")
            return None

        # Skip Twitter/X article URLs (not supported by yt-dlp)
        if '/i/article/' in video_url:
            logger.warning(f"Skipping Twitter/X article URL (not a video): {video_url}")
            return None

        logger.info(f"Starting video download: {video_url}")

        # Run yt-dlp in thread pool to avoid blocking the event loop
        def _sync_download():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(video_url, download=True)
                if info:
                    filename = ydl.prepare_filename(info)
                    logger.info(f"yt-dlp prepared filename: {filename}")
                    logger.info(f"Output directory: {output_path}")
                    logger.info(f"Directory contents: {os.listdir(output_path)}")
                    if os.path.exists(filename):
                        logger.info(f"Video downloaded successfully: {filename}")
                        return filename
                    else:
                        base_path = filename.rsplit('.', 1)[0]
                        for ext in ['.mp4', '.webm', '.mkv', '.mov']:
                            alt_path = base_path + ext
                            if os.path.exists(alt_path):
                                logger.info(f"Video downloaded successfully: {alt_path}")
                                return alt_path
            logger.error(f"Video download failed: {video_url}")
            return None

        filename = await asyncio.to_thread(_sync_download)
        return filename

    except Exception as e:
        error_msg = str(e)
        # Check if it's an unsupported URL error (e.g., Twitter/X article URLs)
        if "Unsupported URL" in error_msg:
            logger.warning(f"URL not supported by yt-dlp, skipping: {video_url}")
            return None
        logger.error(f"Error downloading video {video_url}: {e}")
        return None
