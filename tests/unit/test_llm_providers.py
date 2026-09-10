"""Tests for multi-provider LLM profiles (config resolution, validation, hot-switch)."""

import pytest

from agentnexus.core.config import Settings
from agentnexus.core.llm import AgentLLM
from agentnexus.server.routes.config import ProviderInput, _validate_providers


def _settings_with_providers(active: str = "") -> Settings:
    return Settings(
        llm_model_id="deepseek/deepseek-chat",
        llm_base_url="https://api.deepseek.com",
        llm_api_key="sk-flat",
        llm_providers=[
            {"name": "work", "model_id": "openai/gpt-4o", "base_url": "https://api.openai.com",
             "api_key": "sk-work", "timeout": 30},
            {"name": "local", "model_id": "ollama/qwen3", "base_url": "http://localhost:11434",
             "api_key": "", "timeout": 120},
        ],
        active_provider=active,
    )


class TestActiveProfile:
    def test_flat_fallback_when_no_active_provider(self):
        s = _settings_with_providers(active="")
        model_id, base_url, api_key, timeout = s.get_active_llm_profile()
        assert model_id == "deepseek/deepseek-chat"
        assert base_url == "https://api.deepseek.com"
        assert api_key.get_secret_value() == "sk-flat"
        assert timeout == 60

    def test_active_provider_overrides_flat_fields(self):
        s = _settings_with_providers(active="work")
        model_id, base_url, api_key, timeout = s.get_active_llm_profile()
        assert model_id == "openai/gpt-4o"
        assert base_url == "https://api.openai.com"
        assert api_key.get_secret_value() == "sk-work"
        assert timeout == 30

    def test_unknown_active_name_falls_back_to_flat(self):
        s = _settings_with_providers(active="ghost")
        assert s.get_active_llm_profile()[0] == "deepseek/deepseek-chat"

    def test_llm_property_uses_active_profile(self):
        s = _settings_with_providers(active="local")
        assert s.llm.model_id == "ollama/qwen3"
        assert s.llm.base_url == "http://localhost:11434"
        assert s.llm.timeout == 120
        # Judge settings stay independent of the main-provider switch
        assert s.llm.judge_model_id == "zhipu/glm-4.7-flash"

    def test_provider_base_url_requires_scheme(self):
        with pytest.raises(Exception):
            Settings(llm_providers=[{"name": "bad", "model_id": "m", "base_url": "api.x.com"}])


class TestValidateProviders:
    def test_valid_entries_normalize(self):
        out = _validate_providers([
            ProviderInput(name=" work ", model_id=" openai/gpt-4o ", base_url="https://api.openai.com/", api_key="sk-1"),
        ])
        assert out == [{"name": "work", "model_id": "openai/gpt-4o",
                        "base_url": "https://api.openai.com/", "api_key": "sk-1", "timeout": 60}]

    def test_duplicate_names_rejected(self):
        with pytest.raises(ValueError, match="duplicate"):
            _validate_providers([
                ProviderInput(name="a", model_id="m1", base_url="https://a.com"),
                ProviderInput(name="a", model_id="m2", base_url="https://b.com"),
            ])

    def test_empty_name_rejected(self):
        with pytest.raises(ValueError, match="name"):
            _validate_providers([ProviderInput(name="  ", model_id="m", base_url="https://a.com")])

    def test_missing_model_id_rejected(self):
        with pytest.raises(ValueError, match="model_id"):
            _validate_providers([ProviderInput(name="a", model_id=" ", base_url="https://a.com")])

    def test_base_url_without_scheme_rejected(self):
        with pytest.raises(ValueError, match="http"):
            _validate_providers([ProviderInput(name="a", model_id="m", base_url="api.a.com")])

    def test_mask_placeholder_passes_through_for_merge(self):
        out = _validate_providers([ProviderInput(name="a", model_id="m", base_url="https://a.com", api_key="****")])
        assert out[0]["api_key"] == "****"


class TestAgentLLMConfigure:
    def test_configure_hot_switches_model_and_resets_capability_cache(self):
        llm = AgentLLM(model="openai/gpt-4o", api_key="sk-1", base_url="https://api.openai.com")
        llm._capabilities = object()  # pretend capabilities were detected
        llm._session_tracker = object()

        llm.configure(model="anthropic/claude-sonnet", base_url="https://api.anthropic.com",
                      api_key="sk-2", timeout=45)

        assert llm.model == "anthropic/claude-sonnet"
        assert llm.base_url == "https://api.anthropic.com"
        assert llm.api_key == "sk-2"
        assert llm.timeout == 45
        assert llm._capabilities is None
        assert llm._session_tracker is None

    def test_configure_normalizes_bare_model_id(self):
        llm = AgentLLM(model="openai/gpt-4o", api_key="sk-1", base_url="https://api.openai.com")
        llm.configure(model="deepseek-chat", base_url="https://api.deepseek.com", api_key="sk-3")
        # Bare ids get a provider prefix derived from base_url
        assert "/" in llm.model
