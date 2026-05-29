"""E2E tests for the evaluation pipeline with real LLM scoring.

Exercises: eval metric computation with real LLM-as-Judge calls.
These are lightweight (1-2 samples) to keep costs low.
"""

import pytest

from .e2e_helpers import assert_answer_not_empty


@pytest.mark.e2e
class TestEvalPipeline:
    """Eval pipeline with real LLM judge calls."""

    def test_faithfulness_scorer(self, real_llm):
        """Faithfulness scorer produces a valid score with real LLM."""
        from agentnexus.prompts import format_prompt

        context = "Python 是一种编程语言，由 Guido van Rossum 创建。"
        answer = "Python 由 Guido van Rossum 创建。"

        prompt = format_prompt("eval_faithfulness", context=context, answer=answer)
        response = real_llm.think(prompt)

        assert_answer_not_empty(response)
        try:
            score = float(response.strip())
            assert 0.0 <= score <= 1.0, f"Score {score} outside [0, 1]"
        except ValueError:
            import re
            match = re.search(r"(?:score|分数|得分)[:\s]*(\d*\.?\d+)", response, re.IGNORECASE)
            if match:
                score = float(match.group(1))
                assert 0.0 <= score <= 1.0

    def test_correctness_scorer(self, real_llm):
        """Correctness scorer produces a valid score with real LLM."""
        from agentnexus.prompts import format_prompt

        question = "Python 是谁创造的？"
        ground_truth = "Guido van Rossum"
        answer = "Python 由 Guido van Rossum 于 1991 年创造。"

        prompt = format_prompt(
            "eval_correctness",
            question=question,
            ground_truth=ground_truth,
            answer=answer,
        )
        response = real_llm.think(prompt)

        assert_answer_not_empty(response)

    def test_query_rewrite(self, real_llm):
        """Query rewrite produces a reasonable rewrite with real LLM."""
        from agentnexus.prompts import format_prompt

        query = "那个向量数据库怎么用来搜索东西的"
        prompt = format_prompt("rag_query_rewrite", query=query)
        response = real_llm.think(prompt)

        assert_answer_not_empty(response)
        response_lower = response.lower()
        assert any(kw in response_lower for kw in ["向量", "搜索", "检索", "数据库", "vector", "search"])
