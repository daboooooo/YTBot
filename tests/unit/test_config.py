"""
Unit tests for configuration module
"""

import os
import pytest
from unittest.mock import patch

from ytbot.core.config import (
    get_env_bool,
    get_env_int,
    get_env_float,
    get_env_str,
    get_env_list,
    ConfigTypeError,
    TelegramConfig,
    BotConfig,
    get_config,
    reload_config,
)


class TestEnvHelpers:
    """Tests for environment variable helper functions"""
    
    def test_get_env_bool_true_values(self):
        """Test get_env_bool with true values"""
        true_values = ['true', 'True', 'TRUE', 'yes', 'YES', '1', 't', 'T', 'y', 'Y', 'on', 'ON']
        for value in true_values:
            with patch.dict(os.environ, {'TEST_BOOL': value}):
                assert get_env_bool('TEST_BOOL') is True
    
    def test_get_env_bool_false_values(self):
        """Test get_env_bool with false values"""
        false_values = ['false', 'False', 'FALSE', 'no', 'NO', '0', 'f', 'F', 'n', 'N', 'off', 'OFF']
        for value in false_values:
            with patch.dict(os.environ, {'TEST_BOOL': value}):
                assert get_env_bool('TEST_BOOL') is False
    
    def test_get_env_bool_invalid(self):
        """Test get_env_bool with invalid value raises ConfigTypeError"""
        with patch.dict(os.environ, {'TEST_BOOL': 'invalid'}):
            with pytest.raises(ConfigTypeError):
                get_env_bool('TEST_BOOL', False)
    
    def test_get_env_bool_default(self):
        """Test get_env_bool returns default when env not set"""
        with patch.dict(os.environ, {}, clear=True):
            assert get_env_bool('NONEXISTENT_VAR', True) is True
            assert get_env_bool('NONEXISTENT_VAR', False) is False
    
    def test_get_env_int_valid(self):
        """Test get_env_int with valid integer"""
        with patch.dict(os.environ, {'TEST_INT': '42'}):
            assert get_env_int('TEST_INT') == 42
    
    def test_get_env_int_with_range(self):
        """Test get_env_int with min/max range validation"""
        with patch.dict(os.environ, {'TEST_INT': '5'}):
            assert get_env_int('TEST_INT', min_value=0, max_value=10) == 5
        
        with patch.dict(os.environ, {'TEST_INT': '-5'}):
            with pytest.raises(ConfigTypeError):
                get_env_int('TEST_INT', min_value=0)
        
        with patch.dict(os.environ, {'TEST_INT': '15'}):
            with pytest.raises(ConfigTypeError):
                get_env_int('TEST_INT', max_value=10)
    
    def test_get_env_int_invalid(self):
        """Test get_env_int with invalid value raises exception"""
        with patch.dict(os.environ, {'TEST_INT': 'not_a_number'}):
            with pytest.raises(ConfigTypeError):
                get_env_int('TEST_INT')
    
    def test_get_env_float_valid(self):
        """Test get_env_float with valid float"""
        with patch.dict(os.environ, {'TEST_FLOAT': '3.14'}):
            assert get_env_float('TEST_FLOAT') == 3.14
    
    def test_get_env_str(self):
        """Test get_env_str with various values"""
        with patch.dict(os.environ, {'TEST_STR': 'hello'}):
            assert get_env_str('TEST_STR') == 'hello'
            assert get_env_str('TEST_STR', 'default') == 'hello'
        
        with patch.dict(os.environ, {}, clear=True):
            assert get_env_str('NONEXISTENT', 'default') == 'default'
    
    def test_get_env_str_allowed_values(self):
        """Test get_env_str with allowed values validation"""
        with patch.dict(os.environ, {'TEST_STR': 'valid'}):
            assert get_env_str('TEST_STR', allowed_values=['valid', 'also_valid']) == 'valid'
        
        with patch.dict(os.environ, {'TEST_STR': 'invalid'}):
            with pytest.raises(ConfigTypeError):
                get_env_str('TEST_STR', allowed_values=['valid', 'also_valid'])
    
    def test_get_env_list(self):
        """Test get_env_list with comma-separated values"""
        with patch.dict(os.environ, {'TEST_LIST': 'a,b,c'}):
            assert get_env_list('TEST_LIST') == ['a', 'b', 'c']
        
        with patch.dict(os.environ, {'TEST_LIST': 'a, b, c'}):
            assert get_env_list('TEST_LIST') == ['a', 'b', 'c']
        
        with patch.dict(os.environ, {'TEST_LIST': ''}):
            assert get_env_list('TEST_LIST') == []
        
        with patch.dict(os.environ, {}, clear=True):
            assert get_env_list('NONEXISTENT', ['default']) == ['default']


class TestTelegramConfig:
    """Tests for TelegramConfig"""
    
    def test_default_values(self):
        """Test default configuration values"""
        with patch.dict(os.environ, {}, clear=True):
            config = TelegramConfig()
            assert config.token == ''
            assert config.admin_chat_id == ''
            assert config.allowed_chat_ids == []
    
    def test_allowed_chat_ids_with_admin(self):
        """Test allowed_chat_ids property"""
        with patch.dict(os.environ, {
            'TELEGRAM_BOT_TOKEN': 'test_token',
            'ADMIN_CHAT_ID': '12345'
        }):
            config = TelegramConfig()
            assert config.token == 'test_token'
            assert config.admin_chat_id == '12345'
            assert config.allowed_chat_ids == ['12345']


class TestBotConfig:
    """Tests for BotConfig"""
    
    def test_config_creation(self):
        """Test BotConfig creation"""
        config = BotConfig()
        assert config.telegram is not None
        assert config.nextcloud is not None
        assert config.local_storage is not None
        assert config.download is not None
        assert config.log is not None
        assert config.app is not None
        assert config.monitor is not None
        assert config.security is not None
        assert config.twitter is not None
    
    def test_validation_missing_telegram(self):
        """Test validation with missing Telegram config"""
        with patch.dict(os.environ, {}, clear=True):
            config = BotConfig()
            errors = config.validate()
            assert any('TELEGRAM_BOT_TOKEN' in e for e in errors)
            assert any('ADMIN_CHAT_ID' in e for e in errors)
    
    def test_validation_valid_config(self):
        """Test validation with valid configuration"""
        with patch.dict(os.environ, {
            'TELEGRAM_BOT_TOKEN': 'test_token',
            'ADMIN_CHAT_ID': '12345'
        }):
            config = BotConfig()
            errors = config.validate()
            assert len(errors) == 0
    
    def test_validate_or_raise(self):
        """Test validate_or_raise method"""
        from ytbot.core.config import ConfigValidationError
        
        with patch.dict(os.environ, {}, clear=True):
            config = BotConfig()
            with pytest.raises(ConfigValidationError):
                config.validate_or_raise()
    
    def test_to_dict(self):
        """Test to_dict method"""
        config = BotConfig()
        config_dict = config.to_dict()
        assert 'telegram' in config_dict
        assert 'nextcloud' in config_dict
        assert 'app' in config_dict


class TestGlobalConfig:
    """Tests for global configuration functions"""
    
    def test_get_config(self):
        """Test get_config returns BotConfig instance"""
        config = get_config()
        assert isinstance(config, BotConfig)
        # Should return same instance
        assert get_config() is config
    
    def test_reload_config(self):
        """Test reload_config creates new instance"""
        config1 = get_config()
        config2 = reload_config()
        assert config1 is not config2
        assert isinstance(config2, BotConfig)


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
