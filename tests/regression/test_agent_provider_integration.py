"""Agent + Provider integration regression tests.

Tests the full ReActAgent loop going through the new provider chain:
  ReActAgent.run() → call_llm() → AgentLLM.think() → _call() →
  select_provider() → OpenAIProvider.stream_chat() → mock openai SDK
"""

import json
from unittest.mock import MagicMock, patch

from agentnexus.agents.re_act_agent import ReActAgent
from agentnexus.core.capabilities import ModelCapabilities
from agentnexus.tools.registry import ToolRegistry


def _openai_chunk(content=None, finish_reason=None, tool_calls=None):
    chunk = MagicMock()
    delta = MagicMock()
    delta.content = content
    delta.tool_calls = tool_calls or []
    delta.reasoning_content = None
    chunk.choices = [MagicMock(delta=delta, finish_reason=finish_reason)]
    chunk.usage = None
    return chunk


def _make_llm_with_settings(model, base_url):
    """Create an AgentLLM with patched settings."""
    with patch("agentnexus.core.llm.get_settings") as mock_s:
        mock_s.return_value.llm_model_id = model
        mock_s.return_value.llm_api_key.get_secret_value.return_value = "sk-test"
        mock_s.return_value.llm_base_url = base_url
        mock_s.return_value.llm_timeout = 30
        from agentnexus.core.llm import AgentLLM
        llm = AgentLLM()
    return llm


def _prompt_json_caps():
    """Capabilities that force PROMPT_JSON strategy (no tool calling)."""
    return ModelCapabilities(
        supports_tool_calling=False,
        supports_json_mode=False,
        supports_json_schema=False,
        supports_thinking=False,
        supports_parallel_tool_calls=False,
    )


class TestAgentWithProvider:
    """ReActAgent executing through the real provider chain."""

    @patch("agentnexus.core.llm.trace_manager")
    @patch("agentnexus.core.llm.get_settings")
    def test_agent_single_step_answer(self, mock_settings, mock_trace):
        """Agent answers in one step — JSON answer parsed by FSM."""
        mock_settings.return_value.llm_model_id = "openai/gpt-4"
        mock_settings.return_value.llm_api_key.get_secret_value.return_value = "sk-test"
        mock_settings.return_value.llm_base_url = "https://api.openai.com"
        mock_settings.return_value.llm_timeout = 30
        mock_trace.active = None

        answer_json = json.dumps({"answer": "42"})
        client = MagicMock()
        client.chat.completions.create.return_value = iter([
            _openai_chunk(content=answer_json, finish_reason="stop"),
        ])

        llm = _make_llm_with_settings("openai/gpt-4", "https://api.openai.com")
        te = ToolRegistry()
        agent = ReActAgent(llm, te, max_steps=3)

        with patch("agentnexus.core.providers.openai_provider.OpenAI", return_value=client):
            result = agent.run("What is the meaning of life?")

        assert result.answer is not None
        assert "42" in result.answer

    @patch("agentnexus.core.llm.detect_capabilities")
    @patch("agentnexus.core.llm.trace_manager")
    @patch("agentnexus.core.llm.get_settings")
    def test_agent_tool_call_then_answer(self, mock_settings, mock_trace, mock_caps):
        """Agent parses tool request from JSON, calls tool, then answers."""
        mock_settings.return_value.llm_model_id = "deepseek/deepseek-v4-flash"
        mock_settings.return_value.llm_api_key.get_secret_value.return_value = "sk-test"
        mock_settings.return_value.llm_base_url = "https://api.deepseek.com"
        mock_settings.return_value.llm_timeout = 30
        mock_trace.active = None
        mock_caps.return_value = _prompt_json_caps()

        tool_call_json = json.dumps({
            "thought": "I need to search",
            "tool": "web_search",
            "params": {"query": "test"},
        })
        answer_json = json.dumps({"answer": "Found it"})

        call_count = [0]
        def make_chunks(**kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return iter([_openai_chunk(content=tool_call_json, finish_reason="stop")])
            return iter([_openai_chunk(content=answer_json, finish_reason="stop")])

        llm = _make_llm_with_settings("deepseek/deepseek-v4-flash", "https://api.deepseek.com")
        te = ToolRegistry()
        te.register_tool("web_search", "搜索", lambda query: f"Results for: {query}")

        agent = ReActAgent(llm, te, max_steps=3)

        with patch("agentnexus.core.providers.openai_provider.OpenAI") as mock_cls:
            mock_client = MagicMock()
            mock_client.chat.completions.create.side_effect = make_chunks
            mock_cls.return_value = mock_client
            result = agent.run("Search for test")

        assert result.answer is not None
        assert "Found" in result.answer
        assert call_count[0] >= 2

    @patch("agentnexus.core.llm.detect_capabilities")
    @patch("agentnexus.core.llm.trace_manager")
    @patch("agentnexus.core.llm.get_settings")
    def test_agent_multi_step_with_provider(self, mock_settings, mock_trace, mock_caps):
        """Agent does multiple steps through the provider chain."""
        mock_settings.return_value.llm_model_id = "openai/gpt-4"
        mock_settings.return_value.llm_api_key.get_secret_value.return_value = "sk-test"
        mock_settings.return_value.llm_base_url = "https://api.openai.com"
        mock_settings.return_value.llm_timeout = 30
        mock_trace.active = None
        mock_caps.return_value = _prompt_json_caps()

        step1 = json.dumps({"thought": "Step 1", "tool": "step_one", "params": {"input": "a"}})
        step2 = json.dumps({"thought": "Step 2", "tool": "step_two", "params": {"input": "b"}})
        final = json.dumps({"answer": "Done"})

        call_count = [0]
        def make_chunks(**kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return iter([_openai_chunk(content=step1, finish_reason="stop")])
            if call_count[0] == 2:
                return iter([_openai_chunk(content=step2, finish_reason="stop")])
            return iter([_openai_chunk(content=final, finish_reason="stop")])

        llm = _make_llm_with_settings("openai/gpt-4", "https://api.openai.com")
        te = ToolRegistry()
        te.register_tool("step_one", "Step 1", lambda input: f"R1:{input}")
        te.register_tool("step_two", "Step 2", lambda input: f"R2:{input}")

        agent = ReActAgent(llm, te, max_steps=5)

        with patch("agentnexus.core.providers.openai_provider.OpenAI") as mock_cls:
            mock_client = MagicMock()
            mock_client.chat.completions.create.side_effect = make_chunks
            mock_cls.return_value = mock_client
            result = agent.run("Do two steps")

        assert result.answer is not None
        assert "Done" in result.answer
        assert call_count[0] == 3

    @patch("agentnexus.core.llm.trace_manager")
    @patch("agentnexus.core.llm.get_settings")
    def test_agent_anthropic_uses_litellm_path(self, mock_settings, mock_trace):
        """Agent with anthropic model goes through LiteLLM, not provider."""
        mock_settings.return_value.llm_model_id = "anthropic/claude-4.5"
        mock_settings.return_value.llm_api_key.get_secret_value.return_value = "sk-test"
        mock_settings.return_value.llm_base_url = "https://api.anthropic.com"
        mock_settings.return_value.llm_timeout = 30
        mock_trace.active = None

        answer_json = json.dumps({"answer": "Claude answer"})
        litellm_chunk = MagicMock()
        litellm_chunk.choices = [MagicMock(
            delta=MagicMock(content=answer_json, tool_calls=[], reasoning_content=None),
            finish_reason="stop",
        )]
        litellm_chunk.usage = MagicMock(prompt_tokens=10, completion_tokens=5, total_tokens=15)

        llm = _make_llm_with_settings("anthropic/claude-4.5", "https://api.anthropic.com")
        te = ToolRegistry()
        agent = ReActAgent(llm, te, max_steps=3)

        with patch("litellm.completion", return_value=iter([litellm_chunk])):
            with patch("agentnexus.core.providers.openai_provider.OpenAI") as mock_openai:
                result = agent.run("Hello Claude")

        assert result.answer is not None
        assert "Claude" in result.answer
        mock_openai.assert_not_called()


class TestAgentProviderEdgeCases:
    """Edge cases for agent + provider interaction."""

    @patch("agentnexus.core.llm.trace_manager")
    @patch("agentnexus.core.llm.get_settings")
    def test_agent_handles_empty_provider_response(self, mock_settings, mock_trace):
        """Agent gracefully handles empty response from provider."""
        mock_settings.return_value.llm_model_id = "openai/gpt-4"
        mock_settings.return_value.llm_api_key.get_secret_value.return_value = "sk-test"
        mock_settings.return_value.llm_base_url = "https://api.openai.com"
        mock_settings.return_value.llm_timeout = 30
        mock_trace.active = None

        client = MagicMock()
        client.chat.completions.create.return_value = iter([
            _openai_chunk(content="", finish_reason="stop"),
        ])

        llm = _make_llm_with_settings("openai/gpt-4", "https://api.openai.com")
        te = ToolRegistry()
        agent = ReActAgent(llm, te, max_steps=3)

        with patch("agentnexus.core.providers.openai_provider.OpenAI", return_value=client):
            result = agent.run("Hello")

        assert result is not None

    @patch("agentnexus.core.llm.trace_manager")
    @patch("agentnexus.core.llm.get_settings")
    def test_agent_provider_fallback_on_failure(self, mock_settings, mock_trace):
        """Provider fails → LiteLLM takes over for the full run."""
        mock_settings.return_value.llm_model_id = "deepseek/deepseek-v4-flash"
        mock_settings.return_value.llm_api_key.get_secret_value.return_value = "sk-test"
        mock_settings.return_value.llm_base_url = "https://api.deepseek.com"
        mock_settings.return_value.llm_timeout = 30
        mock_trace.active = None

        answer_json = json.dumps({"answer": "Fallback answer"})
        litellm_chunk = MagicMock()
        litellm_chunk.choices = [MagicMock(
            delta=MagicMock(content=answer_json, tool_calls=[], reasoning_content=None),
            finish_reason="stop",
        )]
        litellm_chunk.usage = MagicMock(prompt_tokens=5, completion_tokens=3, total_tokens=8)

        llm = _make_llm_with_settings("deepseek/deepseek-v4-flash", "https://api.deepseek.com")
        te = ToolRegistry()
        agent = ReActAgent(llm, te, max_steps=3)

        with patch("agentnexus.core.providers.openai_provider.OpenAI", side_effect=ConnectionError("fail")):
            with patch("litellm.completion", return_value=iter([litellm_chunk])):
                result = agent.run("Test question")

        assert result.answer is not None
        assert "Fallback" in result.answer
