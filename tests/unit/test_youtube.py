"""
Unit tests for YouTube platform handler
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
import asyncio

from ytbot.platforms.youtube import YouTubeHandler
from ytbot.core.types import ContentType, ContentInfo, DownloadResult


class TestYouTubeHandler:
    """Tests for YouTubeHandler"""
    
    @pytest.fixture
    def handler(self):
        """Create a YouTubeHandler instance"""
        return YouTubeHandler()
    
    def test_init(self, handler):
        """Test handler initialization"""
        assert handler.name == "YouTube"
        assert ContentType.VIDEO in handler.supported_content_types
        assert ContentType.AUDIO in handler.supported_content_types
        assert ContentType.PLAYLIST in handler.supported_content_types
    
    def test_can_handle_youtube_url(self, handler):
        """Test can_handle with YouTube URLs"""
        youtube_urls = [
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "https://youtu.be/dQw4w9WgXcQ",
            "https://youtube.com/watch?v=dQw4w9WgXcQ",
            "https://www.youtube.com/playlist?list=PL1234567890",
        ]
        
        for url in youtube_urls:
            assert handler.can_handle(url), f"Should handle: {url}"
    
    def test_can_handle_non_youtube_url(self, handler):
        """Test can_handle with non-YouTube URLs"""
        non_youtube_urls = [
            "https://twitter.com/user/status/123456",
            "https://example.com/video",
            "not_a_url",
        ]
        
        for url in non_youtube_urls:
            assert not handler.can_handle(url), f"Should not handle: {url}"
    
    def test_is_playlist(self, handler):
        """Test is_playlist detection"""
        playlist_urls = [
            "https://www.youtube.com/playlist?list=PL1234567890",
            "https://youtube.com/watch?v=abc&list=PL1234567890",
        ]
        
        for url in playlist_urls:
            assert handler.is_playlist(url), f"Should be playlist: {url}"
        
        non_playlist_urls = [
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "https://youtu.be/dQw4w9WgXcQ",
        ]
        
        for url in non_playlist_urls:
            assert not handler.is_playlist(url), f"Should not be playlist: {url}"
    
    def test_get_playlist_id(self, handler):
        """Test playlist ID extraction"""
        url = "https://www.youtube.com/playlist?list=PL1234567890"
        assert handler.get_playlist_id(url) == "PL1234567890"
        
        url = "https://www.youtube.com/watch?v=abc&list=PLabcdef"
        assert handler.get_playlist_id(url) == "PLabcdef"
        
        # Non-playlist URL
        url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        assert handler.get_playlist_id(url) is None
    
    def test_select_best_audio_format(self, handler):
        """Test audio format selection"""
        # Test priority format selection
        formats = [
            {'format_id': '140', 'acodec': 'aac', 'vcodec': 'none', 'abr': 128},
            {'format_id': '251', 'acodec': 'opus', 'vcodec': 'none', 'abr': 160},
            {'format_id': '250', 'acodec': 'opus', 'vcodec': 'none', 'abr': 70},
        ]
        
        # Should select priority format 251
        result = handler.select_best_audio_format(formats)
        assert result == '251'
        
        # Test without priority formats - should select highest bitrate
        formats_no_priority = [
            {'format_id': '140', 'acodec': 'aac', 'vcodec': 'none', 'abr': 128},
            {'format_id': '250', 'acodec': 'opus', 'vcodec': 'none', 'abr': 70},
        ]
        
        result = handler.select_best_audio_format(formats_no_priority)
        assert result == '140'  # Highest bitrate
        
        # Test with no audio formats
        assert handler.select_best_audio_format([]) is None
    
    def test_select_best_video_format(self, handler):
        """Test video format selection"""
        formats = [
            {'format_id': '137', 'vcodec': 'avc1', 'acodec': 'none', 'height': 1080, 'fps': 30},
            {'format_id': '136', 'vcodec': 'avc1', 'acodec': 'none', 'height': 720, 'fps': 30},
            {'format_id': '135', 'vcodec': 'avc1', 'acodec': 'none', 'height': 480, 'fps': 30},
        ]
        
        # Should select priority format 137 (1080p)
        result = handler.select_best_video_format(formats)
        assert result == '137'
        
        # Test with max_height filter
        result = handler.select_best_video_format(formats, max_height=720)
        assert result == '136'  # 720p
        
        # Test without priority format
        formats_no_priority = [
            {'format_id': '136', 'vcodec': 'avc1', 'acodec': 'none', 'height': 720, 'fps': 30},
            {'format_id': '135', 'vcodec': 'avc1', 'acodec': 'none', 'height': 480, 'fps': 60},
        ]
        
        result = handler.select_best_video_format(formats_no_priority)
        assert result == '136'  # Highest quality
        
        # Test with no video formats
        assert handler.select_best_video_format([]) is None
    
    def test_setup_download_options_audio(self, handler):
        """Test download options setup for audio"""
        opts = handler._setup_download_options(
            "/tmp/test",
            ContentType.AUDIO,
            format_id=None
        )
        
        assert 'outtmpl' in opts
        assert 'format' in opts
        assert 'postprocessors' in opts
        assert opts['postprocessors'][0]['key'] == 'FFmpegExtractAudio'
    
    def test_setup_download_options_video(self, handler):
        """Test download options setup for video"""
        opts = handler._setup_download_options(
            "/tmp/test",
            ContentType.VIDEO,
            format_id=None
        )
        
        assert 'outtmpl' in opts
        assert 'format' in opts
        assert 'merge_output_format' in opts
    
    def test_setup_download_options_with_format_id(self, handler):
        """Test download options with specific format ID"""
        opts = handler._setup_download_options(
            "/tmp/test",
            ContentType.VIDEO,
            format_id="137+140"
        )
        
        assert opts['format'] == "137+140"
    
    def test_find_downloaded_file_audio(self, handler, tmp_path):
        """Test finding downloaded audio file"""
        # Create a mock audio file
        audio_file = tmp_path / "test.mp3"
        audio_file.write_text("mock audio")
        
        result = handler._find_downloaded_file(str(tmp_path), ContentType.AUDIO)
        assert result == audio_file
    
    def test_find_downloaded_file_video(self, handler, tmp_path):
        """Test finding downloaded video file"""
        # Create a mock video file
        video_file = tmp_path / "test.mp4"
        video_file.write_text("mock video")
        
        result = handler._find_downloaded_file(str(tmp_path), ContentType.VIDEO)
        assert result == video_file
    
    def test_find_downloaded_file_not_found(self, handler, tmp_path):
        """Test finding file when none exists"""
        result = handler._find_downloaded_file(str(tmp_path), ContentType.VIDEO)
        assert result is None


class TestYouTubeHandlerAsync:
    """Async tests for YouTubeHandler"""
    
    @pytest.fixture
    def handler(self):
        return YouTubeHandler()
    
    @pytest.mark.asyncio
    async def test_get_content_info(self, handler):
        """Test getting content info"""
        # Mock yt_dlp
        mock_info = {
            'title': 'Test Video',
            'description': 'Test Description',
            'duration': 120,
            'thumbnail': 'https://example.com/thumb.jpg',
            'uploader': 'Test Uploader',
            'upload_date': '20240101',
            'formats': [],
        }
        
        with patch('yt_dlp.YoutubeDL') as mock_ydl:
            mock_ydl_instance = MagicMock()
            mock_ydl_instance.extract_info.return_value = mock_info
            mock_ydl.return_value.__enter__.return_value = mock_ydl_instance
            
            result = await handler.get_content_info("https://youtube.com/watch?v=test")
            
            assert result is not None
            assert result.title == "Test Video"
            assert result.url == "https://youtube.com/watch?v=test"
    
    @pytest.mark.asyncio
    async def test_get_content_info_failure(self, handler):
        """Test getting content info when extraction fails"""
        with patch('yt_dlp.YoutubeDL') as mock_ydl:
            mock_ydl_instance = MagicMock()
            mock_ydl_instance.extract_info.return_value = None
            mock_ydl.return_value.__enter__.return_value = mock_ydl_instance
            
            result = await handler.get_content_info("https://youtube.com/watch?v=test")
            
            assert result is None
    
    @pytest.mark.asyncio
    async def test_get_format_list_fallback(self, handler):
        """Test fallback format list retrieval"""
        mock_info = {
            'title': 'Test',
            'formats': [
                {'format_id': '137', 'height': 1080},
                {'format_id': '140', 'abr': 128},
            ]
        }
        
        with patch('yt_dlp.YoutubeDL') as mock_ydl:
            mock_ydl_instance = MagicMock()
            mock_ydl_instance.extract_info.return_value = mock_info
            mock_ydl.return_value.__enter__.return_value = mock_ydl_instance
            
            video_info, formats = await handler._get_format_list_fallback("https://youtube.com/watch?v=test")
            
            assert video_info is not None
            assert len(formats) == 2


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
