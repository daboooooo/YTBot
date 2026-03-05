"""
YouTube platform handler for YTBot

Handles YouTube video and playlist downloads with support for various formats and qualities.
"""

import re
import asyncio
import tempfile
import json
import os
from typing import Dict, Any, Optional, List, Tuple
from pathlib import Path

import yt_dlp

from .base import PlatformHandler
from ..core.logger import get_logger
from ..core.config import get_config
from ..core.types import ContentType, ContentInfo, DownloadResult, JSONDict
from ..core.exceptions import YouTubeError, DownloadError
from ..utils.async_utils import run_with_timeout

logger = get_logger(__name__)


class YouTubeHandler(PlatformHandler):
    """YouTube platform handler for downloading videos and playlists"""

    def __init__(self) -> None:
        super().__init__("YouTube")
        self.supported_content_types = [ContentType.VIDEO, ContentType.AUDIO, ContentType.PLAYLIST]
        self.config = get_config()

    def _load_youtube_cookies(self) -> Optional[str]:
        """
        Load YouTube cookies from file.

        Supports three methods (in order of priority):
        1. Load from project root .youtube_cookies.txt file
        2. Load from YOUTUBE_COOKIES_FILE environment variable
        3. Load from YOUTUBE_COOKIES_JSON environment variable (Netscape format)

        Returns:
            Path to cookies file or None if not found
        """
        config = get_config()
        youtube_config = config.youtube
        cookies_file = youtube_config.cookies_file
        cookies_json = youtube_config.cookies_json

        default_cookie_file = '.youtube_cookies.txt'
        if os.path.exists(default_cookie_file):
            logger.info(f"Loaded YouTube cookies from default file: {default_cookie_file}")
            return default_cookie_file

        if cookies_file and os.path.exists(cookies_file):
            logger.info(f"Loaded YouTube cookies from file: {cookies_file}")
            return cookies_file

        if cookies_json:
            temp_file = os.path.join(tempfile.gettempdir(), 'youtube_cookies.txt')
            try:
                with open(temp_file, 'w', encoding='utf-8') as f:
                    f.write(cookies_json)
                logger.info(f"Created temporary YouTube cookies file: {temp_file}")
                return temp_file
            except Exception as e:
                logger.error(f"Failed to create temporary cookies file: {e}")

        return None

    def can_handle(self, url: str) -> bool:
        """Check if this handler can process the given URL"""
        if not self.validate_url(url):
            return False

        # YouTube URL patterns
        youtube_patterns = [
            r'(https?://)?(www\.)?(youtube|youtu|youtube-nocookie)\.(com|be)/',
            r'(https?://)?(www\.)?(youtube\.com/playlist)',
        ]

        url = url.strip()
        return any(re.search(pattern, url) for pattern in youtube_patterns)

    def is_playlist(self, url: str) -> bool:
        """Check if URL is a YouTube playlist"""
        playlist_pattern = (
            r'(https?://)?(www\.)?(youtube|youtu)\.(com|be)/'
            r'(playlist|watch\?.*list=)'
        )
        return re.search(playlist_pattern, url.strip()) is not None

    def get_playlist_id(self, url: str) -> Optional[str]:
        """Extract playlist ID from URL"""
        if not self.is_playlist(url):
            return None

        import urllib.parse
        parsed = urllib.parse.urlparse(url.strip())
        query_params = urllib.parse.parse_qs(parsed.query)

        if 'list' in query_params:
            return query_params['list'][0]

        return None

    async def get_content_info(self, url: str) -> Optional[ContentInfo]:
        """Get information about the content without downloading"""
        try:
            cookies_path = self._load_youtube_cookies()
            
            ydl_opts = {
                'quiet': True,
                'no_warnings': True,
                'extract_flat': True,  # Don't download, just extract info
            }
            
            if cookies_path:
                ydl_opts['cookiefile'] = cookies_path
                logger.info(f"Using cookies file for content info: {cookies_path}")

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = await asyncio.to_thread(ydl.extract_info, url, download=False)

                if not info:
                    return None

                content_type = ContentType.PLAYLIST if self.is_playlist(url) else ContentType.VIDEO

                return ContentInfo(
                    url=url,
                    title=info.get('title', 'Unknown'),
                    description=info.get('description'),
                    duration=info.get('duration'),
                    content_type=content_type,
                    thumbnail_url=info.get('thumbnail'),
                    uploader=info.get('uploader'),
                    upload_date=info.get('upload_date'),
                    file_size_estimate=None,  # Will be determined during download
                    formats=info.get('formats', []),
                    metadata=info
                )

        except Exception as e:
            logger.error(f"Failed to get YouTube content info for {url}: {e}")
            return None

    async def download_content(
        self,
        url: str,
        content_type: ContentType,
        progress_callback: Optional[Any] = None,
        format_id: Optional[str] = None
    ) -> DownloadResult:
        """
        Download content from YouTube.

        Args:
            url: YouTube video URL
            content_type: Type of content (VIDEO or AUDIO)
            progress_callback: Optional progress callback function
            format_id: Optional specific format ID to download

        Returns:
            DownloadResult with download status and file path
        """
        temp_dir = None

        try:
            temp_dir = tempfile.mkdtemp()
            logger.info(f"Created temp directory: {temp_dir}")

            ydl_opts = self._setup_download_options(
                temp_dir, content_type, progress_callback, format_id
            )

            logger.info("🎬 yt-dlp download options:")
            logger.info(f"  URL: {url}")
            logger.info(f"  Content Type: {content_type.value}")
            logger.info(f"  Format ID: {format_id}")
            for key, value in ydl_opts.items():
                if key == 'http_headers':
                    logger.info(f"  {key}: <headers dict>")
                elif key == 'progress_hooks':
                    logger.info(f"  {key}: <callback functions>")
                else:
                    logger.info(f"  {key}: {value}")

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = await asyncio.to_thread(ydl.extract_info, url, download=True)

                if not info:
                    return DownloadResult(
                        success=False,
                        error_message="Failed to extract video information"
                    )

                # Find the downloaded file
                file_path = self._find_downloaded_file(temp_dir, content_type)

                if not file_path:
                    return DownloadResult(
                        success=False,
                        error_message=f"Downloaded {content_type.value} file not found"
                    )

                # Download subtitles
                subtitle_files = await self._download_subtitles(url, temp_dir, ydl_opts)
                if subtitle_files:
                    logger.info(f"Downloaded subtitles: {subtitle_files}")

                # Create content info
                content_info = ContentInfo(
                    url=url,
                    title=info.get('title', 'Unknown'),
                    description=info.get('description'),
                    duration=info.get('duration'),
                    content_type=content_type,
                    thumbnail_url=info.get('thumbnail'),
                    uploader=info.get('uploader'),
                    upload_date=info.get('upload_date'),
                    metadata=info
                )

                return DownloadResult(
                    success=True,
                    file_path=str(file_path),
                    content_info=content_info
                )

        except Exception as e:
            logger.error(f"Failed to download YouTube content: {e}")
            return DownloadResult(
                success=False,
                error_message=str(e)
            )

        finally:
            # Note: We don't clean up temp_dir here as the file needs to be available
            # for upload/storage. Cleanup should be handled by the caller.
            pass

    def get_supported_formats(self, url: str) -> List[JSONDict]:
        """Get available download formats for the content"""
        try:
            cookies_path = self._load_youtube_cookies()
            
            ydl_opts = {
                'quiet': True,
                'no_warnings': True,
            }
            
            if cookies_path:
                ydl_opts['cookiefile'] = cookies_path

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                return info.get('formats', [])

        except Exception as e:
            logger.error(f"Failed to get YouTube formats for {url}: {e}")
            return []

    async def get_format_list(self, url: str) -> Tuple[JSONDict, List[JSONDict]]:
        """
        Get video info and available formats using yt-dlp command line.

        Args:
            url: YouTube video URL

        Returns:
            Tuple of (video_info, formats_list)
        """
        try:
            # Use yt-dlp command to get format list with tv_embedded client
            cmd = [
                'yt-dlp',
                '--extractor-args', 'youtube:player_client=tv_embedded',
                '--list-formats',
                '--dump-json',
                url
            ]

            logger.info(f"Getting format list for: {url}")

            # Run command asynchronously with timeout
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            # Add timeout to prevent hanging
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=self.config.download.timeout
                )
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                logger.error("yt-dlp format list command timed out")
                return await self._get_format_list_fallback(url)

            if process.returncode != 0:
                logger.error(f"yt-dlp command failed: {stderr.decode()}")
                # Fallback to Python API
                return await self._get_format_list_fallback(url)

            # Parse JSON output
            video_info = json.loads(stdout.decode())
            formats = video_info.get('formats', [])

            logger.info(f"Found {len(formats)} formats for video")
            return video_info, formats

        except Exception as e:
            logger.error(f"Failed to get format list: {e}")
            # Fallback to Python API
            return await self._get_format_list_fallback(url)

    async def _get_format_list_fallback(
        self, url: str
    ) -> Tuple[JSONDict, List[JSONDict]]:
        """
        Fallback method to get formats using yt-dlp Python API.

        Args:
            url: YouTube video URL

        Returns:
            Tuple of (video_info, formats_list)
        """
        try:
            cookies_path = self._load_youtube_cookies()
            
            ydl_opts = {
                'quiet': True,
                'no_warnings': True,
                'extractor_args': ['youtube:player_client=tv_embedded'],
            }
            
            if cookies_path:
                ydl_opts['cookiefile'] = cookies_path

            def extract_info():
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    return ydl.extract_info(url, download=False)

            info = await asyncio.to_thread(extract_info)
            formats = info.get('formats', []) if info else []

            return info or {}, formats

        except Exception as e:
            logger.error(f"Fallback format extraction failed: {e}")
            return {}, []

    def select_best_audio_format(self, formats: List[JSONDict]) -> Optional[str]:
        """
        Select the best audio format.

        Priority: 251 (opus 160kbps) > 140 (m4a 128kbps) > highest bitrate

        Args:
            formats: List of available formats

        Returns:
            Format ID string or None
        """
        audio_formats = [
            f for f in formats
            if f.get('acodec') != 'none' and f.get('vcodec') == 'none'
        ]

        if not audio_formats:
            logger.warning("No audio-only formats found")
            return None

        # Priority order for specific format IDs
        priority_ids = ['251', '140']

        for format_id in priority_ids:
            for fmt in audio_formats:
                if fmt.get('format_id') == format_id:
                    logger.info(f"Selected priority audio format: {format_id}")
                    return format_id

        # Fallback: select highest bitrate
        audio_formats.sort(key=lambda f: f.get('abr', 0) or 0, reverse=True)
        best_format = audio_formats[0]
        format_id = best_format.get('format_id')
        logger.info(
            f"Selected best bitrate audio format: {format_id} "
            f"(abr: {best_format.get('abr')}kbps)"
        )

        return format_id

    def select_best_video_format(
        self,
        formats: List[JSONDict],
        max_height: int = 1080
    ) -> Optional[str]:
        """
        Select the best video format.

        Priority: 1080p (137) > highest quality below 1080p

        Args:
            formats: List of available formats
            max_height: Maximum video height (default: 1080)

        Returns:
            Format ID string or None
        """
        video_formats = [
            f for f in formats
            if f.get('vcodec') != 'none' and f.get('acodec') == 'none'
        ]

        if not video_formats:
            logger.warning("No video-only formats found")
            return None

        # Filter by max height
        suitable_formats = [
            f for f in video_formats
            if (f.get('height') or 0) <= max_height
        ]

        if not suitable_formats:
            logger.warning(f"No formats found at or below {max_height}p")
            suitable_formats = video_formats

        # Try to find 1080p (format 137)
        for fmt in suitable_formats:
            if fmt.get('format_id') == '137':
                logger.info("Selected priority video format: 137 (1080p)")
                return '137'

        # Fallback: select highest quality
        suitable_formats.sort(
            key=lambda f: (f.get('height', 0) or 0, f.get('fps', 0) or 0),
            reverse=True
        )

        best_format = suitable_formats[0]
        format_id = best_format.get('format_id')
        logger.info(
            f"Selected best video format: {format_id} "
            f"({best_format.get('height')}p @ {best_format.get('fps')}fps)"
        )

        return format_id

    def _setup_download_options(
        self,
        temp_dir: str,
        content_type: ContentType,
        progress_callback: Optional[Any] = None,
        format_id: Optional[str] = None
    ) -> JSONDict:
        """
        Setup yt-dlp options based on content type.

        Args:
            temp_dir: Temporary directory for downloads
            content_type: Type of content (VIDEO or AUDIO)
            progress_callback: Optional progress callback function
            format_id: Optional specific format ID to download

        Returns:
            Dictionary of yt-dlp options
        """
        download_config = self.config.download

        base_opts: JSONDict = {
            'outtmpl': str(Path(temp_dir) / '%(title).50s.%(ext)s'),
            'quiet': download_config.quiet,
            'no_warnings': download_config.no_warnings,
            'retries': download_config.retries,
            'fragment_retries': download_config.fragment_retries,
            'timeout': download_config.timeout,
            'socket_timeout': download_config.socket_timeout,
            'http_headers': download_config.http_headers,
            'ignoreerrors': download_config.ignore_errors,
            'ignore_no_formats_error': download_config.ignore_no_formats_error,
            'allow_playlist_files': download_config.allow_playlist_files,
            'sleep_interval_requests': download_config.sleep_interval_requests,
            'sleep_interval': download_config.sleep_interval,
            'max_sleep_interval': download_config.max_sleep_interval,
            'prefer_ffmpeg': download_config.prefer_ffmpeg,
        }

        cookies_path = self._load_youtube_cookies()
        if cookies_path:
            base_opts['cookiefile'] = cookies_path
            logger.info(f"Using cookies file for download: {cookies_path}")

        if progress_callback:
            loop = asyncio.get_running_loop()

            def sync_progress_hook(d: Dict[str, Any]) -> None:
                try:
                    future = asyncio.run_coroutine_threadsafe(progress_callback(d), loop)
                    future.result(timeout=5)
                except Exception as e:
                    logger.debug(f"Progress callback error: {e}")

            base_opts['progress_hooks'] = [sync_progress_hook]

        if content_type == ContentType.AUDIO:
            # Audio download options
            if format_id:
                # Use specific format ID
                base_opts.update({
                    'format': format_id,
                    'postprocessors': [{
                        'key': 'FFmpegExtractAudio',
                        'preferredcodec': download_config.audio_codec,
                        'preferredquality': str(download_config.audio_quality),
                    }],
                })
            else:
                # Use default format selection
                base_opts.update({
                    'format': download_config.audio_format,
                    'postprocessors': [{
                        'key': 'FFmpegExtractAudio',
                        'preferredcodec': download_config.audio_codec,
                        'preferredquality': str(download_config.audio_quality),
                    }],
                })
        else:
            # Video download options
            if format_id:
                # Use specific format ID (video+audio merge)
                # Format ID should be in format "video_id+audio_id"
                base_opts.update({
                    'format': format_id,
                    'merge_output_format': download_config.merge_output_format,
                })
            else:
                # Use default format selection
                base_opts.update({
                    'format': download_config.video_format,
                    'merge_output_format': download_config.merge_output_format,
                    'postprocessors': [{
                        'key': 'FFmpegVideoConvertor',
                        'preferedformat': download_config.video_output_format,
                    }],
                })

        return base_opts

    def _find_downloaded_file(
        self,
        temp_dir: str,
        content_type: ContentType
    ) -> Optional[Path]:
        """Find the downloaded file in the temporary directory"""
        temp_path = Path(temp_dir)

        if content_type == ContentType.AUDIO:
            # Look for audio files
            for ext in ['.mp3', '.m4a', '.wav', '.ogg']:
                files = list(temp_path.rglob(f'*{ext}'))
                if files:
                    return files[0]
        else:
            # Look for video files
            for ext in ['.mp4', '.mkv', '.webm', '.avi']:
                files = list(temp_path.rglob(f'*{ext}'))
                if files:
                    return files[0]

        return None

    async def _download_subtitles(
        self,
        url: str,
        temp_dir: str,
        ydl_opts: JSONDict
    ) -> List[str]:
        """Download subtitles for the video"""
        # TODO: Implement subtitle download
        return []
