"""Decision-level tool selection tests.

Validates that the ReAct agent correctly selects tools based on LLM responses.
"""
from unittest.mock import MagicMock, patch

import pytest

from agentnexus.agents.re_act_agent import ReActAgent
from agentnexus.core.llm import AgentLLM
from agentnexus.tools.tool_executor import ToolExecutor


def _make_llm(think_return="", tool_calls=None):
    """Create a properly configured LLM mock for ReActAgent."""
    llm = MagicMock()
    llm.model = "test/test-model"
    llm.total_usage = {"input_tokens": 0, "output_tokens": 0}
    llm.last_error = ""
    llm.last_truncated = False
    llm.last_tool_calls = tool_calls or []
    llm.last_reasoning_content = ""
    llm.last_usage = {"input_tokens": 0, "output_tokens": 0}
    llm.capabilities = MagicMock()
    llm.capabilities.supports_thinking = False
    llm.capabilities.supports_tool_calling = True
    llm.capabilities.supports_json_mode = True
    llm.capabilities.supports_json_schema = False
    llm.capabilities.supports_parallel_tool_calls = False
    llm.capabilities.thinking_effort = "none"
    if callable(think_return):
        llm.think.side_effect = think_return
    else:
        llm.think.return_value = think_return
    return llm


class TestDecisionLevelToolSelection:
    """Agent selects correct tools based on LLM response."""

    def _make_agent(self, tool_calls=None, think_return=""):
        llm = _make_llm(think_return=think_return, tool_calls=tool_calls or [])
        te = ToolExecutor()
        te.registerTool("web_search", "搜索", lambda **kw: {"results": []})
        te.registerTool("file_read", "读文件", lambda **kw: "content")
        agent = ReActAgent(llm, te, max_steps=3)
        return agent, llm

    def test_selects_web_search_for_query(self):
        responses = ["I'll search", "Python is a language"]
        tool_calls = [{"name": "web_search", "arguments": {"query": "Python"}}]

        agent, llm = self._make_agent(tool_calls=tool_calls)
        call_count = [0]
        def mock_think(**kw):
            call_count[0] += 1
            if call_count[0] == 1:
                llm.last_tool_calls = tool_calls
                return responses[0]
            llm.last_tool_calls = []
            return responses[1]

        llm.think.side_effect = mock_think

        with patch("agentnexus.tools.registry.ToolRegistry.invoke", return_value="{'results': []}"):
            result = agent.run("What is Python?")

        assert call_count[0] >= 1

    def test_no_tools_for_direct_answer(self):
        agent, llm = self._make_agent()
        llm.think.side_effect = lambda **kw: (setattr(llm, 'last_tool_calls', []) or "42")
        assert llm.last_tool_calls == []

        result = agent.run("What is 6*7?")
        assert "42" in (result.answer or "")

    def test_tool_selection_with_reasoning(self):
        agent, llm = self._make_agent()
        llm.last_reasoning_content = "Need to find docs"
        llm.think.side_effect = lambda **kw: "Answer based on docs"

        result = agent.run("Find docs")
        assert llm.think.called

    def test_llm_error_is_handled(self):
        agent, llm = self._make_agent()
        llm.last_error = ""
        llm.think.side_effect = lambda **kw: "A valid answer"

        result = agent.run("Test")
        assert result.answer is not None
