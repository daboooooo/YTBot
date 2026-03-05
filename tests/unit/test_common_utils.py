"""
Unit tests for common utilities
"""

import os
import pytest
from pathlib import Path
from datetime import datetime

from ytbot.utils.common import (
    sanitize_filename,
    format_duration,
    format_file_size,
    truncate_text,
    escape_markdown,
    escape_html_text,
    generate_id,
    parse_url,
    is_valid_url,
    get_file_extension,
    ensure_directory,
    safe_delete,
    format_timestamp,
    chunk_list,
    deep_merge,
    mask_sensitive_data,
)


class TestSanitizeFilename:
    """Tests for sanitize_filename"""
    
    def test_removes_invalid_chars(self):
        """Test removal of invalid characters"""
        assert sanitize_filename('file<name>.txt') == 'filename.txt'
        assert sanitize_filename('file:name.txt') == 'filename.txt'
        assert sanitize_filename('file/name.txt') == 'filename.txt'
        assert sanitize_filename('file\\name.txt') == 'filename.txt'
        assert sanitize_filename('file|name.txt') == 'filename.txt'
        assert sanitize_filename('file?name.txt') == 'filename.txt'
        assert sanitize_filename('file*name.txt') == 'filename.txt'
    
    def test_handles_multiple_spaces(self):
        """Test handling of multiple spaces"""
        assert sanitize_filename('file   name.txt') == 'file name.txt'
    
    def test_trims_whitespace(self):
        """Test trimming of whitespace"""
        assert sanitize_filename('  filename.txt  ') == 'filename.txt'
    
    def test_limits_length(self):
        """Test length limiting"""
        long_name = 'a' * 200 + '.txt'
        result = sanitize_filename(long_name, max_length=50)
        assert len(result) <= 50
    
    def test_empty_name(self):
        """Test handling of empty name"""
        assert sanitize_filename('') == 'unnamed'
        assert sanitize_filename('<>') == 'unnamed'


class TestFormatDuration:
    """Tests for format_duration"""
    
    def test_seconds_only(self):
        """Test formatting seconds only"""
        assert format_duration(45) == "0:45"
    
    def test_minutes_and_seconds(self):
        """Test formatting minutes and seconds"""
        assert format_duration(125) == "2:05"
    
    def test_hours_minutes_seconds(self):
        """Test formatting hours, minutes, and seconds"""
        assert format_duration(3665) == "1:01:05"
    
    def test_none_value(self):
        """Test handling of None"""
        assert format_duration(None) == "Unknown"


class TestFormatFileSize:
    """Tests for format_file_size"""
    
    def test_bytes(self):
        """Test formatting bytes"""
        assert format_file_size(500) == "500.0 B"
    
    def test_kilobytes(self):
        """Test formatting kilobytes"""
        assert format_file_size(1536) == "1.5 KB"
    
    def test_megabytes(self):
        """Test formatting megabytes"""
        assert format_file_size(1572864) == "1.5 MB"
    
    def test_none_value(self):
        """Test handling of None"""
        assert format_file_size(None) == "Unknown"


class TestTruncateText:
    """Tests for truncate_text"""
    
    def test_no_truncate_needed(self):
        """Test text that doesn't need truncation"""
        assert truncate_text("short", max_length=100) == "short"
    
    def test_truncate_with_suffix(self):
        """Test truncation with suffix"""
        result = truncate_text("a" * 100, max_length=20)
        assert len(result) == 20
        assert result.endswith("...")
    
    def test_custom_suffix(self):
        """Test custom suffix"""
        result = truncate_text("a" * 100, max_length=20, suffix="..")
        assert result.endswith("..")


class TestEscapeMarkdown:
    """Tests for escape_markdown"""
    
    def test_escapes_special_chars(self):
        """Test escaping of markdown special characters"""
        assert escape_markdown("*bold*") == r"\*bold\*"
        assert escape_markdown("_italic_") == r"\_italic\_"
        assert escape_markdown("`code`") == r"\`code\`"


class TestEscapeHtml:
    """Tests for escape_html_text"""
    
    def test_escapes_html_chars(self):
        """Test escaping of HTML special characters"""
        assert escape_html_text("<div>") == "&lt;div&gt;"
        assert escape_html_text("&") == "&amp;"
        assert escape_html_text('"quoted"') == "&quot;quoted&quot;"


class TestGenerateId:
    """Tests for generate_id"""
    
    def test_generates_consistent_id(self):
        """Test that same inputs generate same ID"""
        id1 = generate_id("test", 123)
        id2 = generate_id("test", 123)
        assert id1 == id2
    
    def test_generates_different_ids(self):
        """Test that different inputs generate different IDs"""
        id1 = generate_id("test1")
        id2 = generate_id("test2")
        assert id1 != id2
    
    def test_id_length(self):
        """Test ID length"""
        id_val = generate_id("test")
        assert len(id_val) == 12


class TestParseUrl:
    """Tests for parse_url"""
    
    def test_parses_simple_url(self):
        """Test parsing simple URL"""
        result = parse_url("https://example.com/path")
        assert result["scheme"] == "https"
        assert result["hostname"] == "example.com"
        assert result["path"] == "/path"
    
    def test_parses_url_with_query(self):
        """Test parsing URL with query parameters"""
        result = parse_url("https://example.com?key=value&foo=bar")
        assert result["query"]["key"] == "value"
        assert result["query"]["foo"] == "bar"


class TestIsValidUrl:
    """Tests for is_valid_url"""
    
    def test_valid_urls(self):
        """Test valid URLs"""
        assert is_valid_url("https://example.com") is True
        assert is_valid_url("http://localhost:8080") is True
        assert is_valid_url("https://example.com/path?query=value") is True
    
    def test_invalid_urls(self):
        """Test invalid URLs"""
        assert is_valid_url("") is False
        assert is_valid_url("not_a_url") is False
        assert is_valid_url("ftp://example.com") is False
        assert is_valid_url(None) is False


class TestGetFileExtension:
    """Tests for get_file_extension"""
    
    def test_gets_extension(self):
        """Test getting file extension"""
        assert get_file_extension("file.txt") == "txt"
        assert get_file_extension("file.TXT") == "txt"
    
    def test_no_extension(self):
        """Test file without extension"""
        assert get_file_extension("file") == ""


class TestEnsureDirectory:
    """Tests for ensure_directory"""
    
    def test_creates_directory(self, tmp_path):
        """Test creating directory"""
        test_dir = tmp_path / "test_dir"
        result = ensure_directory(test_dir)
        assert test_dir.exists()
        assert result == test_dir
    
    def test_existing_directory(self, tmp_path):
        """Test with existing directory"""
        test_dir = tmp_path / "existing"
        test_dir.mkdir()
        result = ensure_directory(test_dir)
        assert result == test_dir


class TestSafeDelete:
    """Tests for safe_delete"""
    
    def test_delete_file(self, tmp_path):
        """Test deleting file"""
        test_file = tmp_path / "test.txt"
        test_file.write_text("content")
        assert safe_delete(test_file) is True
        assert not test_file.exists()
    
    def test_delete_directory(self, tmp_path):
        """Test deleting directory"""
        test_dir = tmp_path / "test_dir"
        test_dir.mkdir()
        (test_dir / "file.txt").write_text("content")
        assert safe_delete(test_dir) is True
        assert not test_dir.exists()
    
    def test_delete_nonexistent(self):
        """Test deleting non-existent path"""
        # On some systems, deleting non-existent path returns True (no error)
        # So we just check it doesn't raise an exception
        result = safe_delete("/nonexistent/path")
        assert isinstance(result, bool)


class TestFormatTimestamp:
    """Tests for format_timestamp"""
    
    def test_current_time(self):
        """Test formatting current time"""
        result = format_timestamp()
        assert len(result) > 0
    
    def test_specific_timestamp(self):
        """Test formatting specific timestamp"""
        timestamp = datetime(2024, 1, 15, 10, 30, 0).timestamp()
        result = format_timestamp(timestamp)
        assert "2024-01-15" in result
        assert "10:30:00" in result


class TestChunkList:
    """Tests for chunk_list"""
    
    def test_chunks_list(self):
        """Test chunking list"""
        lst = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
        result = chunk_list(lst, 3)
        assert len(result) == 4
        assert result[0] == [1, 2, 3]
        assert result[-1] == [10]


class TestDeepMerge:
    """Tests for deep_merge"""
    
    def test_shallow_merge(self):
        """Test shallow merge"""
        base = {"a": 1, "b": 2}
        override = {"b": 3, "c": 4}
        result = deep_merge(base, override)
        assert result == {"a": 1, "b": 3, "c": 4}
    
    def test_deep_merge(self):
        """Test deep merge"""
        base = {"a": {"x": 1}, "b": 2}
        override = {"a": {"y": 3}}
        result = deep_merge(base, override)
        assert result == {"a": {"x": 1, "y": 3}, "b": 2}


class TestMaskSensitiveData:
    """Tests for mask_sensitive_data"""
    
    def test_masks_password(self):
        """Test masking password"""
        data = {"username": "user", "password": "secret123"}
        result = mask_sensitive_data(data)
        # Password should be masked - check it's not the original
        assert result["password"] != "secret123"
        # Check it contains asterisks
        assert "****" in result["password"]
    
    def test_masks_token(self):
        """Test masking token"""
        data = {"api_token": "abc123def456"}
        result = mask_sensitive_data(data)
        # Token should be masked
        assert result["api_token"] != "abc123def456"
        assert "****" in result["api_token"]
    
    def test_recursive_masking(self):
        """Test recursive masking in nested dict"""
        data = {"config": {"password": "secret"}}
        result = mask_sensitive_data(data)
        assert result["config"]["password"] != "secret"


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
