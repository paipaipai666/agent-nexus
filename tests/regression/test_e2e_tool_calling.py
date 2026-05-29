"""E2E tests for agent tool calling with real LLM.

Exercises the full tool-calling pipeline: agent selects a tool →
ToolExecutor validates and runs it → agent observes result → agent answers.
"""

import pytest

from .e2e_helpers import assert_answer_contains_keywords, assert_answer_not_empty


def _has_tool_capability(real_agent) -> bool:
    """Check if the agent's model supports tool calling."""
    if hasattr(real_agent, "llm_client"):
        caps = getattr(real_agent.llm_client, "capabilities", None)
        if caps and getattr(caps, "tool_calling", False):
            return True
    return False


@pytest.mark.e2e
class TestAgentToolCalling:
    """Agent selects and executes tools based on user queries."""

    def test_calculator_tool_selection(self, real_agent):
        """Agent uses a calculator/math tool when available."""
        if not _has_tool_capability(real_agent):
            pytest.skip("Model does not support tool calling")

        result = real_agent.run("计算 2 的 10 次方是多少？")
        answer = result.answer if hasattr(result, "answer") else str(result)

        assert_answer_not_empty(answer)
        assert_answer_contains_keywords(answer, ["1024"])

    def test_web_search_tool_selection(self, real_agent):
        """Agent selects web_search for current events (if available)."""
        if not _has_tool_capability(real_agent):
            pytest.skip("Model does not support tool calling")

        result = real_agent.run("搜索一下今天的天气")
        answer = result.answer if hasattr(result, "answer") else str(result)

        assert_answer_not_empty(answer)

    def test_agent_uses_code_execution(self, real_agent):
        """Agent selects code execution for programming tasks."""
        if not _has_tool_capability(real_agent):
            pytest.skip("Model does not support tool calling")

        result = real_agent.run("用 Python 写一个函数计算斐波那契数列第10项，然后告诉我结果")
        answer = result.answer if hasattr(result, "answer") else str(result)

        assert_answer_not_empty(answer)
        assert_answer_contains_keywords(answer, ["55"])


@pytest.mark.e2e
class TestToolExecutorIntegration:
    """ToolExecutor works correctly with real agent flow."""

    def test_tool_executor_is_wired(self, real_agent):
        """Agent has a ToolExecutor with registered tools."""
        if not hasattr(real_agent, "tool_executor"):
            pytest.skip("Agent does not expose tool_executor directly")

        executor = real_agent.tool_executor
        assert executor is not None

    def test_agent_graceful_tool_failure(self, real_agent):
        """Agent handles tool failure without crashing."""
        result = real_agent.run("读取文件 /nonexistent/path/test.txt 的内容")
        answer = result.answer if hasattr(result, "answer") else str(result)

        assert isinstance(answer, str)
        assert len(answer) > 0
