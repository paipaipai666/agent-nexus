"""复现：只开思考 → 能力探测被跳过 → 强制 PROMPT_JSON → 提示词注入 JSON 格式。

线上现象（ModelPicker）：
  Thinking 绿，原生工具 / JSON Mode / JSON Schema 全灰；
  模型思考里却说「用户要求我输出合法的 JSON」。

链路：
  detect_capabilities(model_thinking=...) 将 from_default_fallback 置 False
  → AgentLLM.capabilities 不再 _merge_probed_capabilities
  → supports_tool_calling 停留在保守默认 False
  → select_strategy → PROMPT_JSON
  → prepare_llm_call 注入 build_json_format_section()
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agentnexus.agents.llm_strategy import prepare_llm_call, build_json_format_section
from agentnexus.agents.react_types import CallingStrategy
from agentnexus.agents import decisions
from agentnexus.core.capabilities import ModelCapabilities, SessionCapabilityTracker


class TestThinkingDoesNotDisableProbe:
    """开思考不应关掉 tool_calling / json_mode 的探测。"""

    def test_only_model_thinking_still_allows_probe(self, temp_agentnexus_home, monkeypatch):
        """只设 model_thinking 时，from_default_fallback 必须仍为 True。"""
        monkeypatch.setenv("AGENTNEXUS_MODEL_THINKING", "true")
        import agentnexus.core.config as cfg_mod
        cfg_mod._settings_cache = None

        from agentnexus.core.capabilities import detect_capabilities
        caps = detect_capabilities("any-model", "https://api.example.com")

        assert caps.supports_thinking is True
        # 关键：thinking 覆盖不应把「其余能力仍是默认值、可探测」也抹掉
        assert caps.from_default_fallback is True, (
            "只配置 model_thinking 时不应跳过 tool_calling/json_mode 探测"
        )

    def test_tool_calling_override_still_blocks_probe(self, temp_agentnexus_home, monkeypatch):
        monkeypatch.setenv("AGENTNEXUS_MODEL_TOOL_CALLING", "true")
        import agentnexus.core.config as cfg_mod
        cfg_mod._settings_cache = None

        from agentnexus.core.capabilities import detect_capabilities
        caps = detect_capabilities("any-model", "https://api.example.com")
        assert caps.supports_tool_calling is True
        assert caps.from_default_fallback is False


class TestProbeThenNativeStrategy:
    """探测到 tool_calling 后必须走 NATIVE，且不注入 JSON 格式。"""

    def _client_with_probe(self, monkeypatch, *, tool_calling: bool, thinking: bool):
        monkeypatch.setenv("AGENTNEXUS_MODEL_THINKING", "true" if thinking else "false")
        import agentnexus.core.config as cfg_mod
        cfg_mod._settings_cache = None

        from agentnexus.core.llm import AgentLLM
        import agentnexus.core.llm as llm_mod
        llm_mod._probe_cache.clear()

        real = AgentLLM.__dict__["_merge_probed_capabilities"]
        client = object.__new__(AgentLLM)
        # Unique endpoint per case so probe cache cannot leak across tests
        client.model = f"test/model-{tool_calling}-{thinking}"
        client.base_url = f"https://api.example.com/v1/{tool_calling}/{thinking}"
        client._capabilities = None
        client._session_tracker = SessionCapabilityTracker()
        monkeypatch.setattr(
            AgentLLM, "_probe_capabilities",
            lambda self: {"tool_calling": tool_calling, "json_mode": False},
        )
        monkeypatch.setattr(
            AgentLLM, "_merge_probed_capabilities",
            lambda self, caps: real(self, caps),
        )
        return client

    def test_thinking_on_plus_probed_tool_calling_selects_native(self, monkeypatch):
        """用户看到的现象反面：thinking 绿 + 探测到工具 → 必须 NATIVE。"""
        client = self._client_with_probe(monkeypatch, tool_calling=True, thinking=True)
        caps = client.capabilities

        assert caps.supports_thinking is True
        assert caps.supports_tool_calling is True, (
            "thinking 开启后仍必须探测/保留 tool_calling，否则 UI 会全灰"
        )

        strategy = decisions.select_strategy(SessionCapabilityTracker(), caps)
        assert strategy is CallingStrategy.NATIVE_TOOLS

    def test_thinking_on_without_tool_calling_falls_to_prompt_json(self, monkeypatch):
        """对照：探测失败才允许 PROMPT_JSON（这时 JSON 格式注入是预期的）。"""
        client = self._client_with_probe(monkeypatch, tool_calling=False, thinking=True)
        caps = client.capabilities
        strategy = decisions.select_strategy(SessionCapabilityTracker(), caps)
        assert strategy is CallingStrategy.PROMPT_JSON


class TestNativeDoesNotInjectJsonFormat:
    def test_native_prepare_does_not_append_json_section(self):
        messages = [{"role": "user", "content": "详细说说 hook 层"}]
        tools, rfmt = prepare_llm_call(CallingStrategy.NATIVE_TOOLS, messages, [{"type": "function"}])
        assert tools is not None
        assert rfmt is None
        assert "合法的 JSON" not in messages[0]["content"]
        assert "== 输出格式" not in messages[0]["content"]

    def test_prompt_json_does_append_json_section(self):
        messages = [{"role": "user", "content": "详细说说 hook 层"}]
        prepare_llm_call(CallingStrategy.PROMPT_JSON, messages, None)
        assert "合法的 JSON" in messages[0]["content"]
        assert build_json_format_section() in messages[0]["content"]


class TestEndToEndThinkingJsonLeak:
    """端到端：thinking 开 + 未探测 tool_calling 时，当前实现会注入 JSON——锁住期望行为。"""

    def test_enable_thinking_must_not_force_prompt_json(self, monkeypatch):
        monkeypatch.setenv("AGENTNEXUS_MODEL_THINKING", "true")
        import agentnexus.core.config as cfg_mod
        cfg_mod._settings_cache = None

        from agentnexus.core.capabilities import detect_capabilities
        from agentnexus.core.llm import AgentLLM

        caps = detect_capabilities("m", "https://x.test/v1")
        client = object.__new__(AgentLLM)
        client.model = "m"
        client.base_url = "https://x.test/v1"
        client._capabilities = None
        client._session_tracker = SessionCapabilityTracker()
        monkeypatch.setattr(
            AgentLLM, "_probe_capabilities",
            lambda self: {"tool_calling": True, "json_mode": True},
        )

        resolved = client.capabilities
        assert resolved.supports_thinking is True
        assert resolved.supports_tool_calling is True, (
            "BUG: model_thinking 阻断探测 → tool_calling 永远 False → PROMPT_JSON + JSON 提示词"
        )

        strategy = decisions.select_strategy(SessionCapabilityTracker(), resolved)
        messages = [{"role": "user", "content": "详细说说 hook 层"}]
        prepare_llm_call(strategy, messages, [{"type": "function"}])
        assert "合法的 JSON" not in messages[0]["content"], (
            f"strategy={strategy} 时不应注入 JSON 格式，实际 content={messages[0]['content']!r}"
        )
