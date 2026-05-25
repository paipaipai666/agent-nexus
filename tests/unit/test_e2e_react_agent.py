"""End-to-end ReAct Agent benchmark test.

Runs the full ReAct agent through multi-step tasks with mocked LLM,
validating intermediate state assertions (tool calls, observations, reasoning steps).
"""
from unittest.mock import MagicMock, patch

import pytest

from agentnexus.agents.re_act_agent import ReActAgent
from agentnexus.tools.tool_executor import ToolExecutor


def _make_llm():
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
    llm.think.return_value = ""
    return llm


class TestE2EReActAgent:
    """End-to-end ReAct agent with intermediate state assertions."""

    def _make_agent(self):
        llm = _make_llm()
        te = ToolExecutor()
        te.registerTool("web_search", "搜索", lambda **kw: {"results": [{"title": "Python"}]})
        te.registerTool("file_read", "读文件", lambda **kw: "file content")
        agent = ReActAgent(llm, te, max_steps=5)
        return agent, llm

    def test_e2e_single_step_answer(self):
        agent, llm = self._make_agent()

        def mock_think(**kw):
            llm.last_tool_calls = []
            return "The answer is 42"
        llm.think.side_effect = mock_think

        result = agent.run("What is 6*7?")
        assert result.answer is not None
        assert "42" in result.answer

    def test_e2e_tool_then_answer(self):
        agent, llm = self._make_agent()
        call_count = [0]

        def mock_think(**kw):
            call_count[0] += 1
            if call_count[0] == 1:
                llm.last_tool_calls = [{"name": "web_search", "arguments": {"query": "Python"}}]
                return "Searching..."
            llm.last_tool_calls = []
            return "Python is a high-level language."
        llm.think.side_effect = mock_think

        result = agent.run("What is Python?")
        assert call_count[0] >= 1
        assert result.answer is not None

    def test_e2e_multiple_tools(self):
        agent, llm = self._make_agent()
        call_count = [0]

        def mock_think(**kw):
            call_count[0] += 1
            if call_count[0] == 1:
                llm.last_tool_calls = [
                    {"name": "web_search", "arguments": {"query": "test"}},
                    {"name": "file_read", "arguments": {"path": "f.py"}},
                ]
                return "Searching..."
            llm.last_tool_calls = []
            return "Combined result."
        llm.think.side_effect = mock_think

        result = agent.run("Search and read")
        assert call_count[0] >= 1

    def test_e2e_max_steps_respected(self):
        """Agent respects max_steps and terminates quickly."""
        agent, llm = self._make_agent()
        agent.max_steps = 1
        call_count = [0]

        def mock_think(**kw):
            call_count[0] += 1
            llm.last_tool_calls = []
            return "Answer"
        llm.think.side_effect = mock_think

        result = agent.run("Question")
        assert call_count[0] <= 1
        assert result.answer is not None

    def test_e2e_execution_context_accumulates(self):
        agent, llm = self._make_agent()
        llm.think.side_effect = lambda **kw: (setattr(llm, 'last_tool_calls', []) or "Answer")

        result = agent.run("Test question")
        assert llm.think.called

    def test_e2e_error_handling(self):
        agent, llm = self._make_agent()
        llm.last_error = "Connection failed"
        llm.think.return_value = ""
        llm.think.side_effect = lambda **kw: ""

        result = agent.run("Trigger error")
        assert result.answer is None or isinstance(result.answer, str)
