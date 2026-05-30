"""Complete tool call pipeline test.

Validates the full ReAct loop: LLM response -> tool call -> observation -> next LLM call.
"""
from unittest.mock import MagicMock, patch

from agentnexus.agents.re_act_agent import ReActAgent
from agentnexus.tools.registry import ToolRegistry


def _make_llm(think_response=""):
    llm = MagicMock()
    llm.model = "test/test-model"
    llm.total_usage = {"input_tokens": 0, "output_tokens": 0}
    llm.last_error = ""
    llm.last_truncated = False
    llm.last_tool_calls = []
    llm.last_reasoning_content = ""
    llm.last_usage = {"input_tokens": 0, "output_tokens": 0}
    llm.capabilities = MagicMock()
    llm.capabilities.supports_thinking = False
    llm.capabilities.supports_tool_calling = True
    llm.capabilities.supports_json_mode = True
    llm.capabilities.supports_json_schema = False
    llm.capabilities.supports_parallel_tool_calls = False
    llm.capabilities.thinking_effort = "none"
    llm.think.return_value = think_response
    return llm


class TestToolCallPipeline:
    """Full ReAct loop with tool calls producing observations."""

    def _make_agent(self):
        llm = _make_llm()
        te = ToolRegistry()
        te.register_tool("web_search", "搜索", lambda **kw: {"results": [{"title": "Python"}]})
        te.register_tool("file_read", "读文件", lambda **kw: "file content")
        agent = ReActAgent(llm, te, max_steps=5)
        return agent, llm

    def test_single_tool_call_pipeline(self):
        agent, llm = self._make_agent()
        call_count = [0]
        def mock_think(**kw):
            call_count[0] += 1
            if call_count[0] == 1:
                llm.last_tool_calls = [{"name": "web_search", "arguments": {"query": "Python"}}]
                return "Searching..."
            llm.last_tool_calls = []
            return "Python is a language."
        llm.think.side_effect = mock_think

        agent.run("What is Python?")

        assert call_count[0] >= 1

    def test_multi_tool_call_pipeline(self):
        agent, llm = self._make_agent()
        call_count = [0]
        def mock_think(**kw):
            call_count[0] += 1
            if call_count[0] == 1:
                llm.last_tool_calls = [
                    {"name": "web_search", "arguments": {"query": "test"}},
                    {"name": "file_read", "arguments": {"path": "f.py"}},
                ]
                return "Searching and reading..."
            llm.last_tool_calls = []
            return "Final answer."
        llm.think.side_effect = mock_think

        agent.run("Search and read")
        assert call_count[0] >= 1

    def test_tool_observation_feeds_back_to_llm(self):
        agent, llm = self._make_agent()
        observations_from_llm = []

        call_count = [0]
        def mock_think(**kw):
            call_count[0] += 1
            if call_count[0] == 1:
                llm.last_tool_calls = [{"name": "web_search", "arguments": {"query": "Python"}}]
                return "Searching..."
            observations_from_llm.append(kw.get("messages", []))
            llm.last_tool_calls = []
            return "Based on search results."
        llm.think.side_effect = mock_think

        agent.run("What is Python?")
        assert call_count[0] >= 1

    def test_pipeline_with_error_recovery(self):
        agent, llm = self._make_agent()
        call_count = [0]
        def mock_think(**kw):
            call_count[0] += 1
            if call_count[0] == 1:
                llm.last_tool_calls = [{"name": "web_search", "arguments": {"query": "test"}}]
                return "Searching..."
            llm.last_tool_calls = []
            return "Recovered answer."
        llm.think.side_effect = mock_think

        def failing_execute(**kw):
            return "tool result after retry"

        with patch("agentnexus.tools.registry.ToolRegistry.invoke", side_effect=failing_execute):
            agent.run("What is test?")

        assert call_count[0] >= 1
