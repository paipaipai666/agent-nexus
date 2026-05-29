"""E2E tests for memory system with real LLM extraction.

Exercises: memory extraction from Q&A → structured storage →
cross-session retrieval → memory-informed answers.
"""

import pytest

from .e2e_helpers import assert_answer_not_empty


@pytest.mark.e2e
class TestMemoryExtraction:
    """Memory extraction from real LLM conversations."""

    def test_memory_extract_from_qa(self, real_llm, temp_agentnexus_home):
        """Extract structured memories from a Q&A pair."""
        from agentnexus.memory.manager import MemoryManager

        mgr = MemoryManager(llm=real_llm)

        question = "我是一名 Python 开发者，我喜欢用 pytest 写测试"
        answer = "了解！您是一名 Python 开发者，偏好使用 pytest 进行测试。"

        memories = mgr.extract_memories(question, answer)

        assert memories, "Memory extraction returned empty list"
        categories = [m.get("category", "") for m in memories]
        assert any(c in ["user_preference", "entity_fact", "conclusion"] for c in categories)

    def test_memory_save_and_retrieve(self, real_llm, temp_agentnexus_home):
        """Save memories and retrieve them later."""
        from agentnexus.memory.long_term import get_long_term_memory

        ltm = get_long_term_memory()

        ltm.save(
            session_id="test_session",
            content="用户偏好中文回复",
            category="user_preference",
            importance=0.8,
        )

        memories = ltm.list_recent(5)
        assert memories, "Long-term memory retrieval returned empty"
        assert any("中文" in m.get("content", "") for m in memories)

    def test_memory_informs_agent_answer(self, real_agent, temp_agentnexus_home):
        """Agent uses memory context in subsequent turns."""
        from agentnexus.memory.long_term import get_long_term_memory

        ltm = get_long_term_memory()
        ltm.save(
            session_id="test_session",
            content="用户正在开发一个天气查询应用",
            category="entity_fact",
            importance=0.9,
        )

        result = real_agent.run("我在做什么项目？")
        answer = result.answer if hasattr(result, "answer") else str(result)

        assert_answer_not_empty(answer)
