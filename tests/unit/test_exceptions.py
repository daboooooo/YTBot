"""
Unit tests for exceptions module
"""

import pytest
from ytbot.core.exceptions import (
    YTBotError,
    ConfigError,
    ConfigValidationError,
    YouTubeError,
    TwitterError,
    DownloadError,
    StorageError,
    NextcloudError,
    get_user_friendly_message,
)


class TestYTBotError:
    """Tests for base YTBotError"""
    
    def test_basic_error(self):
        """Test basic error creation"""
        error = YTBotError("Test message")
        assert str(error) == "[YTBOT] Test message"
        assert error.message == "Test message"
        assert error.error_code == "YTBOT"
    
    def test_error_with_details(self):
        """Test error with details"""
        error = YTBotError(
            "Test message",
            error_code="TEST001",
            details={"key": "value"}
        )
        assert error.error_code == "TEST001"
        assert error.details == {"key": "value"}
    
    def test_error_with_cause(self):
        """Test error with cause"""
        cause = ValueError("Original error")
        error = YTBotError("Test message", cause=cause)
        assert error.cause is cause
    
    def test_to_dict(self):
        """Test error serialization to dict"""
        error = YTBotError(
            "Test message",
            error_code="TEST001",
            details={"key": "value"}
        )
        error_dict = error.to_dict()
        assert error_dict["error_code"] == "TEST001"
        assert error_dict["message"] == "Test message"
        assert error_dict["details"] == {"key": "value"}


class TestConfigErrors:
    """Tests for configuration errors"""
    
    def test_config_error(self):
        """Test ConfigError"""
        error = ConfigError("Config error")
        assert isinstance(error, YTBotError)
        assert "CONFIG" in error.error_code
    
    def test_config_validation_error(self):
        """Test ConfigValidationError"""
        error = ConfigValidationError("Validation failed")
        assert isinstance(error, ConfigError)
        assert "VALIDATION" in error.error_code


class TestPlatformErrors:
    """Tests for platform errors"""
    
    def test_youtube_error(self):
        """Test YouTubeError"""
        error = YouTubeError("Video not found", video_id="abc123")
        assert error.platform == "YouTube"
        assert error.video_id == "abc123"
        assert error.details["video_id"] == "abc123"
    
    def test_twitter_error(self):
        """Test TwitterError"""
        error = TwitterError("Tweet not found", tweet_id="123456")
        assert error.platform == "Twitter/X"
        assert error.tweet_id == "123456"
        assert error.details["tweet_id"] == "123456"


class TestDownloadErrors:
    """Tests for download errors"""
    
    def test_download_error(self):
        """Test DownloadError"""
        error = DownloadError(
            "Download failed",
            download_id="dl123",
            url="https://example.com"
        )
        assert error.download_id == "dl123"
        assert error.url == "https://example.com"
        assert error.details["download_id"] == "dl123"


class TestStorageErrors:
    """Tests for storage errors"""
    
    def test_storage_error(self):
        """Test StorageError"""
        error = StorageError(
            "Upload failed",
            storage_type="nextcloud",
            file_path="/path/to/file"
        )
        assert error.storage_type == "nextcloud"
        assert error.file_path == "/path/to/file"
    
    def test_nextcloud_error(self):
        """Test NextcloudError"""
        error = NextcloudError("Nextcloud error")
        assert error.storage_type == "nextcloud"


class TestUserFriendlyMessages:
    """Tests for user-friendly error messages"""
    
    def test_config_validation_message(self):
        """Test message for ConfigValidationError"""
        error = ConfigValidationError("Missing token")
        msg = get_user_friendly_message(error)
        assert "配置错误" in msg
    
    def test_youtube_error_message(self):
        """Test message for YouTubeError"""
        error = YouTubeError("Video unavailable")
        msg = get_user_friendly_message(error)
        assert "YouTube" in msg
    
    def test_twitter_error_message(self):
        """Test message for TwitterError"""
        error = TwitterError("Tweet deleted")
        msg = get_user_friendly_message(error)
        assert "Twitter/X" in msg
    
    def test_download_timeout_message(self):
        """Test message for DownloadTimeoutError"""
        from ytbot.core.exceptions import DownloadTimeoutError
        error = DownloadTimeoutError("Timeout")
        msg = get_user_friendly_message(error)
        assert "超时" in msg
    
    def test_generic_error_message(self):
        """Test message for generic Exception"""
        error = Exception("Generic error")
        msg = get_user_friendly_message(error)
        assert "操作失败" in msg


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
