"""Tests for the multi-model provider schema: migration, resolution, judge, overrides."""

import pytest

from agentnexus.core import capabilities
from agentnexus.core.capabilities import detect_capabilities
from agentnexus.core.config import (
    LLMProvider,
    ModelEntry,
    ModelOverride,
    Settings,
)
from agentnexus.core.llm import AgentLLM
from agentnexus.server.routes.config import ProviderInput, _validate_providers


def _provider(name="sf", models=None, base="https://api.siliconflow.cn/v1", key="sk-x"):
    return LLMProvider(
        name=name,
        models=models or [ModelEntry(model_id="deepseek-ai/DeepSeek-V4-Flash")],
        base_url=base,
        api_key=key,
    )


class TestFlatConfigMigration:
    def test_flat_config_seeds_default_provider(self):
        s = Settings(llm_model_id="m1", llm_base_url="https://x.example.com/v1",
                     llm_api_key="sk-k")
        assert [p.name for p in s.llm_providers] == ["default"]
        assert s.llm_providers[0].models[0].model_id == "m1"
        assert s.active_model == "default/m1"

    def test_seeded_provider_resolves_profile(self):
        s = Settings(llm_model_id="m1", llm_base_url="https://x.example.com/v1",
                     llm_api_key="sk-k")
        model, base, key, timeout = s.get_active_llm_profile()
        assert model == "m1"
        assert base == "https://x.example.com/v1"
        assert key.get_secret_value() == "sk-k"

    def test_existing_providers_not_reseeded(self):
        p = _provider()
        s = Settings(llm_providers=[p], llm_model_id="flat-model")
        assert [x.name for x in s.llm_providers] == ["sf"]

    def test_legacy_provider_model_id_migrates(self):
        p = LLMProvider(name="or", model_id="openai/gpt-4o",
                        base_url="https://openrouter.ai/api/v1", api_key="sk-k")
        assert [m.model_id for m in p.models] == ["openai/gpt-4o"]

    def test_legacy_active_provider_maps_to_first_model(self):
        p = _provider(name="sf")
        s = Settings(llm_providers=[p], active_provider="sf")
        assert s.active_model == "sf/deepseek-ai/DeepSeek-V4-Flash"


class TestModelResolution:
    def test_find_model_selector(self):
        p = _provider(models=[ModelEntry(model_id="a"), ModelEntry(model_id="b")])
        s = Settings(llm_providers=[p])
        found = s.find_model("sf/b")
        assert found is not None
        assert found[1].model_id == "b"
        assert s.find_model("sf/nope") is None
        assert s.find_model("nope/x") is None
        assert s.find_model("badformat") is None

    def test_active_model_beats_legacy_active_provider(self):
        p1 = _provider(name="p1", models=[ModelEntry(model_id="m1")])
        p2 = _provider(name="p2", models=[ModelEntry(model_id="m2")])
        s = Settings(llm_providers=[p1, p2], active_model="p2/m2", active_provider="p1")
        assert s.get_active_llm_profile()[0] == "m2"

    def test_unknown_active_model_falls_back_to_flat(self):
        s = Settings(llm_model_id="flat", llm_base_url="https://x.example.com",
                     llm_api_key="sk-k", active_model="gone/missing")
        # resolve_active_model returns None; flat fallback kicks in
        model, *_ = s.get_active_llm_profile()
        assert model == "flat"


class TestJudgeProfile:
    def test_judge_selector_wins(self):
        p = _provider(models=[ModelEntry(model_id="judge-model")])
        s = Settings(llm_providers=[p], judge_model="sf/judge-model",
                     judge_api_key="sk-judge", judge_model_id="legacy/x")
        model, base, key, _ = s.get_judge_profile()
        assert model == "judge-model"
        assert key.get_secret_value() == "sk-x"  # provider's own key

    def test_judge_follows_task_model_by_default(self):
        p = _provider(models=[ModelEntry(model_id="task-model")])
        s = Settings(llm_providers=[p], active_model="sf/task-model")
        model, *_ = s.get_judge_profile()
        assert model == "task-model"

    def test_explicit_legacy_judge_fields_win(self):
        s = Settings(judge_api_key="sk-judge", judge_model_id="fam/x",
                     judge_base_url="https://j.example.com")
        model, base, key, _ = s.get_judge_profile()
        assert model == "fam/x"
        assert base == "https://j.example.com"
        assert key.get_secret_value() == "sk-judge"


class TestPerModelCapabilityOverride:
    def test_override_applies(self, monkeypatch):
        entry = ModelEntry(
            model_id="custom-model",
            override=ModelOverride(supports_vision=True, context_length=32_000,
                                   supports_tool_calling=False),
        )
        p = _provider(models=[entry], base="https://x.example.com/v1")
        s = Settings(llm_providers=[p])
        monkeypatch.setattr(capabilities, "get_settings", lambda: s)

        caps = detect_capabilities("custom-model", "https://x.example.com/v1")
        assert caps.supports_vision is True
        assert caps.max_context_tokens == 32_000
        assert caps.supports_tool_calling is False

    def test_no_override_leaves_detected_values(self, monkeypatch):
        p = _provider(models=[ModelEntry(model_id="custom-model2")],
                      base="https://x.example.com/v1")
        s = Settings(llm_providers=[p])
        monkeypatch.setattr(capabilities, "get_settings", lambda: s)
        caps = detect_capabilities("custom-model2", "https://x.example.com/v1")
        assert caps.supports_vision is False  # conservative default, no crash

    def test_find_model_override_entry_matches_base_url_loosely(self):
        p = _provider(base="https://x.example.com/v1/")
        s = Settings(llm_providers=[p])
        assert s.find_model_override_entry(
            "deepseek-ai/DeepSeek-V4-Flash", "https://X.example.com/v1") is not None


class TestValidateProviders:
    def test_models_passthrough_with_override(self):
        out = _validate_providers([ProviderInput(
            name="sf", base_url="https://x.example.com/v1", api_key="sk-k",
            models=[{"model_id": "m1", "override": {"supports_vision": True}}],
        )])
        assert out[0]["models"][0]["model_id"] == "m1"
        assert out[0]["models"][0]["override"]["supports_vision"] is True

    def test_legacy_model_id_seeds_models(self):
        out = _validate_providers([ProviderInput(
            name="sf", base_url="https://x.example.com/v1", model_id="m1")])
        assert out[0]["models"] == [{"model_id": "m1", "override": None}]

    def test_rejects_duplicate_model_ids(self):
        with pytest.raises(ValueError):
            _validate_providers([ProviderInput(
                name="sf", base_url="https://x.example.com/v1",
                models=[{"model_id": "m1"}, {"model_id": "m1"}])])

    def test_rejects_duplicate_names(self):
        with pytest.raises(ValueError, match="duplicate"):
            _validate_providers([
                ProviderInput(name="a", base_url="https://a.com", models=[{"model_id": "m1"}]),
                ProviderInput(name="a", base_url="https://b.com", models=[{"model_id": "m2"}]),
            ])

    def test_rejects_empty_name(self):
        with pytest.raises(ValueError, match="name"):
            _validate_providers([ProviderInput(name="  ", base_url="https://a.com",
                                               models=[{"model_id": "m"}])])

    def test_rejects_base_url_without_scheme(self):
        with pytest.raises(ValueError, match="http"):
            _validate_providers([ProviderInput(name="a", base_url="api.a.com",
                                               models=[{"model_id": "m"}])])

    def test_masked_key_kept_on_merge_semantics(self):
        # api_key None flows through; the route merges the stored key
        out = _validate_providers([ProviderInput(
            name="sf", base_url="https://x.example.com/v1")])
        assert out[0]["api_key"] is None


class TestActiveProfileLegacy:
    """Legacy provider-granularity selectors keep working after the schema upgrade."""

    def _settings(self, active_provider=""):
        return Settings(
            llm_model_id="deepseek/deepseek-chat",
            llm_base_url="https://api.deepseek.com",
            llm_api_key="sk-flat",
            llm_providers=[
                {"name": "work", "model_id": "openai/gpt-4o", "base_url": "https://api.openai.com",
                 "api_key": "sk-work", "timeout": 30},
            ],
            active_provider=active_provider,
        )

    def test_active_provider_overrides_flat_fields(self):
        s = self._settings(active_provider="work")
        model_id, base_url, api_key, timeout = s.get_active_llm_profile()
        assert model_id == "openai/gpt-4o"
        assert base_url == "https://api.openai.com"
        assert api_key.get_secret_value() == "sk-work"
        assert timeout == 30

    def test_unknown_active_provider_falls_back_to_flat(self):
        s = self._settings(active_provider="ghost")
        assert s.get_active_llm_profile()[0] == "deepseek/deepseek-chat"

    def test_llm_property_uses_active_profile(self):
        s = self._settings(active_provider="work")
        assert s.llm.model_id == "openai/gpt-4o"
        assert s.llm.base_url == "https://api.openai.com"
        assert s.llm.timeout == 30

    def test_provider_base_url_requires_scheme(self):
        with pytest.raises(Exception):
            Settings(llm_providers=[{"name": "bad", "model_id": "m", "base_url": "api.x.com"}])


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
