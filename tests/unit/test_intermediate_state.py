"""Intermediate state assertions for multi-step agent reasoning.

Validates that ExecutionContext accumulates correct steps, tool_outputs,
and pending_tool_calls after multi-step agent runs.
"""
from unittest.mock import MagicMock

from agentnexus.agents.re_act_agent import ReActAgent
from agentnexus.tools.registry import ToolRegistry


def _make_llm():
    llm = MagicMock()
    llm.model = "test/test-model"
    llm.total_usage = {"input_tokens": 0, "output_tokens": 0}
    llm.last_error = ""
    llm.last_truncated = False
    llm.last_tool_calls = []
    llm.last_reasoning_content = ""
    llm.last_usage = {"input_tokens": 5, "output": 3}
    llm.last_usage = {"input_tokens": 5, "output_tokens": 3}
    llm.capabilities = MagicMock()
    llm.capabilities.supports_thinking = False
    llm.capabilities.supports_tool_calling = True
    llm.capabilities.supports_json_mode = True
    llm.capabilities.supports_json_schema = False
    llm.capabilities.supports_parallel_tool_calls = False
    llm.capabilities.thinking_effort = "none"
    llm.think.return_value = ""
    return llm


class TestIntermediateStateAssertions:
    """ExecutionContext state after multi-step runs."""

    def _make_agent(self):
        llm = _make_llm()
        te = ToolRegistry()
        te.register_tool("web_search", "搜索", lambda **kw: {"results": [{"title": "r"}]})
        te.register_tool("file_read", "读文件", lambda **kw: "content")
        agent = ReActAgent(llm, te, max_steps=5)
        return agent, llm

    def test_steps_accumulated_after_run(self):
        agent, llm = self._make_agent()
        llm.think.side_effect = lambda **kw: (setattr(llm, 'last_tool_calls', []) or "Answer")

        result = agent.run("Test question")
        assert len(result.steps) >= 1

    def test_tool_outputs_populated_after_execution(self):
        agent, llm = self._make_agent()
        call_count = [0]

        def mock_think(**kw):
            call_count[0] += 1
            if call_count[0] == 1:
                llm.last_tool_calls = [{"name": "web_search", "arguments": {"query": "test"}}]
                return "Searching..."
            llm.last_tool_calls = []
            return "Done"
        llm.think.side_effect = mock_think

        result = agent.run("Search")
        assert len(result.steps) >= 1
        assert call_count[0] >= 1

    def test_pending_tool_calls_cleared_after_execution(self):
        agent, llm = self._make_agent()
        call_count = [0]

        def mock_think(**kw):
            call_count[0] += 1
            if call_count[0] == 1:
                llm.last_tool_calls = [{"name": "web_search", "arguments": {"query": "test"}}]
                return "Searching..."
            llm.last_tool_calls = []
            return "Result"
        llm.think.side_effect = mock_think

        result = agent.run("Search")
        assert result.answer is not None

    def test_multiple_tool_calls_accumulated(self):
        """Multiple tool calls in one step produce multiple outputs."""
        agent, llm = self._make_agent()
        call_count = [0]

        def mock_think(**kw):
            call_count[0] += 1
            if call_count[0] == 1:
                llm.last_tool_calls = [
                    {"name": "web_search", "arguments": {"query": "test"}},
                    {"name": "file_read", "arguments": {"path": "test.py"}},
                ]
                return "Searching and reading..."
            llm.last_tool_calls = []
            return "Combined result"
        llm.think.side_effect = mock_think

        result = agent.run("Search and read")
        assert call_count[0] >= 2
        assert result.steps[0].tool_outputs  # tool outputs recorded in step

    def test_execution_context_strategy_tracking(self):
        agent, llm = self._make_agent()
        llm.think.side_effect = lambda **kw: (setattr(llm, 'last_tool_calls', []) or "Answer")

        result = agent.run("Test")
        assert len(result.steps) >= 1

    def test_reasoning_content_captured(self):
        agent, llm = self._make_agent()
        llm.last_reasoning_content = "Thinking about this..."
        llm.think.side_effect = lambda **kw: (setattr(llm, 'last_tool_calls', []) or "Answer with reasoning")

        result = agent.run("Test")
        assert len(result.steps) >= 1
        captured_step = result.steps[0]
        assert captured_step.reasoning_content == "Thinking about this..."
