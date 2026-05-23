"""Tests for model capability detection."""
from agentnexus.core.capabilities import (
    SessionCapabilityTracker,
    _lookup_registry,
    detect_capabilities,
)


class TestStaticRegistry:
    def test_exact_match_deepseek_v4_pro(self):
        caps = _lookup_registry("deepseek/deepseek-v4-pro")
        assert caps.supports_tool_calling is True
        assert caps.supports_thinking is True
        assert caps.max_context_tokens == 262_144

    def test_wildcard_match_deepseek(self):
        caps = _lookup_registry("deepseek/some-unknown-model")
        assert caps.supports_tool_calling is True
        assert caps.supports_json_mode is True

    def test_wildcard_match_openai(self):
        caps = _lookup_registry("openai/gpt-4o-mini")
        assert caps.supports_tool_calling is True
        assert caps.supports_json_schema is True

    def test_ultimate_fallback(self):
        caps = _lookup_registry("completely/unknown-model-v99")
        assert caps.supports_tool_calling is False
        assert caps.supports_thinking is False

    def test_zhipu_unknown_is_conservative(self):
        caps = _lookup_registry("zhipu/glm-3-turbo")
        assert caps.supports_tool_calling is False

    def test_anthropic_claude_46_has_thinking(self):
        caps = _lookup_registry("anthropic/claude-4.6-sonnet")
        assert caps.supports_thinking is True
        assert caps.supports_parallel_tool_calls is True


class TestDetectCapabilities:
    def test_respects_config_override(self, temp_agentnexus_home, monkeypatch):
        monkeypatch.setenv("AGENTNEXUS_MODEL_TOOL_CALLING", "false")
        monkeypatch.setenv("AGENTNEXUS_MODEL_THINKING", "true")
        # Force reload settings
        import agentnexus.core.config as cfg_mod
        cfg_mod._settings_cache = None

        caps = detect_capabilities("deepseek/deepseek-v4-pro")
        assert caps.supports_tool_calling is False  # overridden
        assert caps.supports_thinking is True       # overridden


class TestSessionCapabilityTracker:
    def test_available_by_default(self):
        tracker = SessionCapabilityTracker()
        assert tracker.is_available("tool_calling", True) is True
        assert tracker.is_available("tool_calling", False) is False

    def test_disabled_after_max_retries(self):
        tracker = SessionCapabilityTracker()
        assert tracker.mark_failed("json_mode") is True   # 1 >= default max_retries=1
        assert tracker.mark_failed("json_mode") is True   # still disabled
        assert tracker.is_available("json_mode", True) is False

    def test_reset_clears_failures(self):
        tracker = SessionCapabilityTracker()
        tracker.mark_failed("tool_calling")
        assert tracker.is_available("tool_calling", True) is False
        tracker.reset("tool_calling")
        assert tracker.is_available("tool_calling", True) is True

    def test_no_cross_feature_contamination(self):
        tracker = SessionCapabilityTracker()
        tracker.mark_failed("tool_calling")
        assert tracker.is_available("json_mode", True) is True

    def test_max_retries_custom(self):
        tracker = SessionCapabilityTracker()
        assert tracker.mark_failed("tool_calling", max_retries=3) is False  # 1 < 3
        assert tracker.is_available("tool_calling", True) is True
        tracker.mark_failed("tool_calling", max_retries=3)  # 2
        assert tracker.mark_failed("tool_calling", max_retries=3) is True   # 3 >= 3
        assert tracker.is_available("tool_calling", True) is False
