"""E2E tests for multi-turn conversations with real LLM.

Exercises: context accumulation across turns, memory persistence,
and conversation coherence.
"""

import pytest

from .e2e_helpers import assert_answer_contains_keywords, assert_answer_not_empty


@pytest.mark.e2e
class TestMultiTurnConversation:
    """Agent maintains context across multiple conversation turns."""

    def test_context_preservation(self, real_agent):
        """Agent remembers context from earlier in the conversation."""
        result1 = real_agent.run("我叫小明，我是一名数据科学家")
        answer1 = result1.answer if hasattr(result1, "answer") else str(result1)
        assert_answer_not_empty(answer1)

        result2 = real_agent.run("我叫什么名字？我的职业是什么？")
        answer2 = result2.answer if hasattr(result2, "answer") else str(result2)

        assert_answer_not_empty(answer2)
        assert_answer_contains_keywords(answer2, ["小明"], min_hits=1)

    def test_topic_switch(self, real_agent):
        """Agent handles topic switches gracefully."""
        result1 = real_agent.run("Python 的 GIL 是什么？")
        answer1 = result1.answer if hasattr(result1, "answer") else str(result1)
        assert_answer_not_empty(answer1)

        result2 = real_agent.run("那 JavaScript 的事件循环呢？")
        answer2 = result2.answer if hasattr(result2, "answer") else str(result2)
        assert_answer_not_empty(answer2)

    def test_incremental_task(self, real_agent):
        """Agent handles incremental task building across turns."""
        result1 = real_agent.run("帮我写一个 Python 函数，计算两个数的和")
        answer1 = result1.answer if hasattr(result1, "answer") else str(result1)
        assert_answer_not_empty(answer1)

        result2 = real_agent.run("现在修改这个函数，让它支持任意多个数的和")
        answer2 = result2.answer if hasattr(result2, "answer") else str(result2)
        assert_answer_not_empty(answer2)
        assert_answer_contains_keywords(answer2, ["*args", "sum", "def"], min_hits=1)
