"""Tests for model capability detection (no vendor catalog)."""

from agentnexus.core.capabilities import (
    ModelCapabilities,
    SessionCapabilityTracker,
    detect_capabilities,
    resolve_ctx_max,
)


class TestDetectCapabilities:
    def test_unknown_model_is_conservative_and_flagged(self, temp_agentnexus_home):
        caps = detect_capabilities("completely/unknown-model-v99", "https://example.com")
        assert caps.supports_tool_calling is False
        assert caps.supports_thinking is False
        assert caps.max_context_tokens == 0
        assert caps.from_default_fallback is True

    def test_respects_config_override(self, temp_agentnexus_home, monkeypatch):
        monkeypatch.setenv("AGENTNEXUS_MODEL_TOOL_CALLING", "false")
        monkeypatch.setenv("AGENTNEXUS_MODEL_THINKING", "true")
        import agentnexus.core.config as cfg_mod
        cfg_mod._settings_cache = None

        caps = detect_capabilities("any-model", "https://api.example.com")
        assert caps.supports_tool_calling is False
        assert caps.supports_thinking is True
        assert caps.from_default_fallback is False


class TestResolveCtxMax:
    def test_unknown_returns_none(self):
        assert resolve_ctx_max("totally-unknown-model-xyz") is None

    def test_override_sets_context(self, monkeypatch):
        from agentnexus.core.config import LLMProvider, ModelEntry, ModelOverride, Settings
        import agentnexus.core.capabilities as caps_mod

        entry = ModelEntry(
            model_id="m1",
            override=ModelOverride(context_length=50_000),
        )
        p = LLMProvider(name="p1", base_url="https://x.example.com/v1", models=[entry])
        s = Settings(llm_providers=[p])
        monkeypatch.setattr(caps_mod, "get_settings", lambda: s)
        assert resolve_ctx_max("m1", "https://x.example.com/v1") == 50_000


class TestSessionTracker:
    def test_mark_failed_disables(self):
        t = SessionCapabilityTracker()
        assert t.mark_failed("tool_calling") is True
        assert t.is_available("tool_calling", True) is False

    def test_reset_clears(self):
        t = SessionCapabilityTracker()
        t.mark_failed("thinking")
        t.reset("thinking")
        assert t.is_available("thinking", True) is True

    def test_defaults(self):
        c = ModelCapabilities()
        assert c.from_default_fallback is True
        assert c.max_context_tokens == 0
