"""Tests for ConfigService."""

from unittest.mock import MagicMock

from agentnexus.services.config import ConfigService


class TestConfigService:
    def test_get_settings_returns_settings(self):
        settings = MagicMock()
        service = ConfigService(settings)
        assert service.get_settings() is settings

    def test_get_settings_with_real_settings(self, temp_agentnexus_home):
        from agentnexus.core.config import get_settings

        settings = get_settings()
        service = ConfigService(settings)
        assert service.get_settings() is settings
        assert service.get_settings().llm_model_id == "deepseek/deepseek-v4-flash"

    def test_extension_status_none_when_no_manager(self):
        service = ConfigService(MagicMock())
        assert service.extension_status() is None

    def test_extension_status_delegates_to_manager(self):
        manager = MagicMock()
        manager.status.return_value = {"plugins": 3}
        service = ConfigService(MagicMock(), extension_manager=manager)
        assert service.extension_status() == {"plugins": 3}
        manager.status.assert_called_once_with()
