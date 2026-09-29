"""Session thinking-effort override: storage, validation, and LLM effort resolution."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


class TestChatServiceThinkingEffort:
    def _svc(self):
        from agentnexus.services.chat import ChatService

        svc = ChatService.__new__(ChatService)
        svc._sessions = {}
        svc._agents = {}
        svc._thinking_effort = {}
        return svc

    def test_get_default_is_none(self):
        svc = self._svc()
        assert svc.get_thinking_effort("nope") is None

    def test_set_and_get(self):
        svc = self._svc()
        svc._sessions["s1"] = SimpleNamespace(id="s1")
        assert svc.set_thinking_effort("s1", "high") == "high"
        assert svc.get_thinking_effort("s1") == "high"

    def test_set_follow_clears_override(self):
        svc = self._svc()
        svc._sessions["s1"] = SimpleNamespace(id="s1")
        svc.set_thinking_effort("s1", "low")
        assert svc.set_thinking_effort("s1", None) is None
        assert svc.get_thinking_effort("s1") is None

    def test_unknown_session_raises(self):
        svc = self._svc()
        with pytest.raises(KeyError):
            svc.set_thinking_effort("missing", "low")

    def test_invalid_effort_raises(self):
        svc = self._svc()
        svc._sessions["s1"] = SimpleNamespace(id="s1")
        with pytest.raises(ValueError):
            svc.set_thinking_effort("s1", "xhigh")

    def test_binds_to_existing_agent(self):
        svc = self._svc()
        svc._sessions["s1"] = SimpleNamespace(id="s1")
        agent = MagicMock()
        svc._agents["s1"] = agent
        svc.set_thinking_effort("s1", "high")
        agent.set_thinking_effort.assert_called_once_with("high")


class TestLLMEffortOverride:
    def _llm(self, caps_thinking_effort="medium", supports_thinking=True):
        from agentnexus.core.llm import AgentLLM

        llm = AgentLLM.__new__(AgentLLM)
        llm._capabilities = SimpleNamespace(
            supports_thinking=supports_thinking,
            thinking_effort=caps_thinking_effort,
            supports_tool_calling=False,
            supports_json_mode=False,
            supports_json_schema=False,
            supports_parallel_tool_calls=False,
            max_output_tokens=256,
        )
        llm._session_tracker = MagicMock()
        llm._session_tracker.is_available = MagicMock(return_value=True)
        llm.model = "m"
        llm.api_key = "k"
        llm.base_url = "http://x"
        llm.timeout = 30
        return llm

    def _capture_effort(self, llm, **kwargs):
        captured = {}

        class Prov:
            def stream_chat(self, **kw):
                captured.update(kw)
                from agentnexus.core.providers.base import StreamResult

                return StreamResult(text="ok", tool_calls=[])

        result = llm._call_via_provider(Prov(), [], 0.0, None, None, **kwargs)
        assert result.text == "ok"
        return captured.get("reasoning_effort")

    def test_none_effort_disables(self):
        llm = self._llm()
        assert self._capture_effort(llm, thinking=None, effort="none") == "none"

    def test_high_override(self):
        llm = self._llm(caps_thinking_effort="low")
        assert self._capture_effort(llm, thinking=None, effort="high") == "high"

    def test_falls_back_to_caps(self):
        llm = self._llm(caps_thinking_effort="medium")
        assert self._capture_effort(llm, thinking=None, effort=None) == "medium"

    def test_override_ignored_when_thinking_disabled(self):
        llm = self._llm(supports_thinking=False)
        assert self._capture_effort(llm, thinking=False, effort="high") == "none"
