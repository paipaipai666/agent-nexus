"""Performance tests for query expansion — deduplication, question detection, query expansion."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agentnexus.rag.query_expansion import (
    dedupe_preserve_order,
    expand_queries,
    looks_like_question,
)

# ── dedupe_preserve_order ─────────────────────────────────────


class TestDedupePreserveOrder:
    def test_dedupe_1000_items_no_dupes(self, benchmark):
        items = [f"unique query {i}" for i in range(1000)]

        result = benchmark(dedupe_preserve_order, items)
        assert len(result) == 1000

    def test_dedupe_1000_items_high_dupes(self, benchmark):
        items = [f"query {i % 50}" for i in range(1000)]

        result = benchmark(dedupe_preserve_order, items)
        assert len(result) == 50

    def test_dedupe_1000_items_case_variants(self, benchmark):
        items = [f"Query {i % 100}".lower() if i % 2 == 0 else f"QUERY {i % 100}" for i in range(1000)]

        result = benchmark(dedupe_preserve_order, items)
        assert len(result) <= 100


# ── looks_like_question ───────────────────────────────────────


class TestLooksLikeQuestion:
    @pytest.mark.parametrize("query", [
        "什么是机器学习？",
        "How does Python garbage collection work?",
        "请帮我写一段代码",
        "deepseek v4 model architecture",
        "这个问题应该怎么解决呢？",
    ])
    def test_looks_like_question_various(self, benchmark, query):
        result = benchmark(looks_like_question, query)
        assert isinstance(result, bool)

    def test_looks_like_question_long_query(self, benchmark):
        query = "这是一段很长的查询文本，" * 50 + "请问如何实现这个功能？"

        result = benchmark(looks_like_question, query)
        assert result is True


# ── expand_queries ────────────────────────────────────────────


def _make_mock_llm(responses: list[str]) -> MagicMock:
    """Create a mock LLM that returns responses in sequence."""
    mock = MagicMock()
    mock.think = MagicMock(side_effect=responses)
    mock.last_truncated = False
    return mock


class TestExpandQueries:
    def test_expand_queries_fast(self, benchmark, perf_env):
        llm = _make_mock_llm([
            "rewritten query",
            "- expanded query 1\n- expanded query 2\n- expanded query 3",
        ])

        result = benchmark(expand_queries, "test query", llm)
        assert isinstance(result, list)
        assert len(result) >= 1

    def test_expand_queries_single_rewrite(self, benchmark, perf_env):
        llm = _make_mock_llm(["rewritten query"])

        result = benchmark(expand_queries, "test query", llm)
        assert isinstance(result, list)
        assert len(result) >= 1
