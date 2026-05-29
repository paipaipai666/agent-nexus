"""E2E tests for agent reasoning with real LLM calls.

These tests exercise the full ReActAgent pipeline: prompt construction →
LLM call → response parsing → answer emission. No mocking.
"""

import pytest

from .e2e_helpers import (
    assert_answer_contains_keywords,
    assert_answer_length,
    assert_answer_not_empty,
)


@pytest.mark.e2e
class TestAgentBasicReasoning:
    """Agent answers simple factual questions without tools."""

    def test_simple_factual_question(self, real_agent):
        """Agent answers a straightforward knowledge question."""
        result = real_agent.run("Python 是什么编程语言？请用一句话回答。")
        answer = result.answer if hasattr(result, "answer") else str(result)

        assert_answer_not_empty(answer)
        assert_answer_length(answer, min_chars=5, max_chars=500)
        assert_answer_contains_keywords(answer, ["Python", "编程", "语言"])

    def test_math_reasoning(self, real_agent):
        """Agent performs basic math reasoning."""
        result = real_agent.run("15 + 27 等于多少？只回答数字。")
        answer = result.answer if hasattr(result, "answer") else str(result)

        assert_answer_not_empty(answer)
        assert "42" in answer

    def test_agent_returns_structured_result(self, real_agent):
        """Agent result has expected attributes (steps, answer, etc.)."""
        result = real_agent.run("你好，请简短回复。")

        assert hasattr(result, "answer") or isinstance(result, str)
        if hasattr(result, "steps"):
            assert isinstance(result.steps, list)
        if hasattr(result, "answer"):
            assert_answer_not_empty(result.answer)

    def test_agent_handles_chinese_and_english(self, real_agent):
        """Agent handles mixed language input gracefully."""
        result = real_agent.run("What is 机器学习? Explain in one sentence.")
        answer = result.answer if hasattr(result, "answer") else str(result)

        assert_answer_not_empty(answer)
        assert_answer_length(answer, min_chars=10, max_chars=1000)


@pytest.mark.e2e
class TestAgentErrorHandling:
    """Agent handles edge cases gracefully."""

    def test_empty_input(self, real_agent):
        """Agent handles empty-ish input without crashing."""
        result = real_agent.run("...")
        answer = result.answer if hasattr(result, "answer") else str(result)
        assert isinstance(answer, str)

    def test_very_long_input(self, real_agent):
        """Agent handles long input without crashing."""
        long_input = "请总结以下内容：" + "这是一个测试。" * 200
        result = real_agent.run(long_input)
        answer = result.answer if hasattr(result, "answer") else str(result)
        assert isinstance(answer, str)
