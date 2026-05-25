"""Comprehensive prompt template tests.

Covers all prompt template files, variable injection, and edge cases.
"""
from unittest.mock import patch

import pytest

import agentnexus.prompts as prompts


class TestAllPromptTemplatesExist:
    """Every .txt template in prompts/ can be loaded."""

    TEMPLATE_NAMES = [
        "react",
        "contextual",
        "contextual_generation",
        "contextual_retrieval",
        "memory_extract",
        "memory_summarize",
        "eval_generate",
        "eval_answer_relevancy",
        "eval_correctness",
        "eval_faithfulness",
        "eval_precision",
        "eval_recall",
        "eval_relevancy",
        "rag_hyde",
        "rag_multi_query",
        "rag_query_rewrite",
    ]

    @pytest.mark.parametrize("name", TEMPLATE_NAMES)
    def test_load_prompt_exists(self, name):
        content = prompts.load_prompt(name)
        assert isinstance(content, str)
        assert len(content) > 0


class TestFormatPromptVariableInjection:
    """format_prompt injects variables correctly for each template."""

    def test_react_injects_tools_question_history(self):
        with patch("agentnexus.prompts.load_prompt", return_value="{tools}\n{question}\n{history}"):
            result = prompts.format_prompt("react", tools="[t1]", question="Q", history="H")
        assert "[t1]" in result
        assert "Q" in result
        assert "H" in result

    def test_memory_extract_injects_question_answer(self):
        with patch("agentnexus.prompts.load_prompt", return_value="{question}\n{answer}"):
            result = prompts.format_prompt("memory_extract", question="Q", answer="A")
        assert "Q" in result
        assert "A" in result

    def test_memory_summarize_injects_history(self):
        with patch("agentnexus.prompts.load_prompt", return_value="Summary: {history}"):
            result = prompts.format_prompt("memory_summarize", history="turn1\nturn2")
        assert "turn1" in result

    def test_eval_generate_injects_context_question(self):
        with patch("agentnexus.prompts.load_prompt", return_value="{context}\n{question}"):
            result = prompts.format_prompt("eval_generate", context="ctx", question="q")
        assert "ctx" in result
        assert "q" in result

    def test_rag_hyde_injects_question(self):
        with patch("agentnexus.prompts.load_prompt", return_value="{question}"):
            result = prompts.format_prompt("rag_hyde", question="what is AI?")
        assert "AI" in result

    def test_rag_multi_query_injects_question(self):
        with patch("agentnexus.prompts.load_prompt", return_value="{question}"):
            result = prompts.format_prompt("rag_multi_query", question="test")
        assert "test" in result

    def test_rag_query_rewrite_injects_question(self):
        with patch("agentnexus.prompts.load_prompt", return_value="{question}"):
            result = prompts.format_prompt("rag_query_rewrite", question="test")
        assert "test" in result

    def test_contextual_injects_context(self):
        with patch("agentnexus.prompts.load_prompt", return_value="{context}"):
            result = prompts.format_prompt("contextual", context="ctx")
        assert "ctx" in result

    def test_contextual_generation_injects_context(self):
        with patch("agentnexus.prompts.load_prompt", return_value="{context}"):
            result = prompts.format_prompt("contextual_generation", context="ctx")
        assert "ctx" in result

    def test_contextual_retrieval_injects_context(self):
        with patch("agentnexus.prompts.load_prompt", return_value="{context}"):
            result = prompts.format_prompt("contextual_retrieval", context="ctx")
        assert "ctx" in result

    def test_eval_answer_relevancy_injects_question_answer(self):
        with patch("agentnexus.prompts.load_prompt", return_value="{question}\n{answer}"):
            result = prompts.format_prompt("eval_answer_relevancy", question="Q", answer="A")
        assert "Q" in result

    def test_eval_correctness_injects_question_answer(self):
        with patch("agentnexus.prompts.load_prompt", return_value="{question}\n{answer}"):
            result = prompts.format_prompt("eval_correctness", question="Q", answer="A")
        assert "Q" in result

    def test_eval_faithfulness_injects_context_question(self):
        with patch("agentnexus.prompts.load_prompt", return_value="{context}\n{question}"):
            result = prompts.format_prompt("eval_faithfulness", context="C", question="Q")
        assert "C" in result

    def test_eval_precision_injects_question(self):
        with patch("agentnexus.prompts.load_prompt", return_value="{question}"):
            result = prompts.format_prompt("eval_precision", question="Q")
        assert "Q" in result

    def test_eval_recall_injects_question(self):
        with patch("agentnexus.prompts.load_prompt", return_value="{question}"):
            result = prompts.format_prompt("eval_recall", question="Q")
        assert "Q" in result

    def test_eval_relevancy_injects_question(self):
        with patch("agentnexus.prompts.load_prompt", return_value="{question}"):
            result = prompts.format_prompt("eval_relevancy", question="Q")
        assert "Q" in result


class TestFormatPromptEdgeCases:
    """Edge cases for format_prompt."""

    def test_missing_variable_raises_keyerror(self):
        with patch("agentnexus.prompts.load_prompt", return_value="{missing}"):
            with pytest.raises(KeyError):
                prompts.format_prompt("test")

    def test_extra_kwargs_ignored(self):
        with patch("agentnexus.prompts.load_prompt", return_value="{date}"):
            result = prompts.format_prompt("test", extra="ignored")
        assert "extra" not in result

    def test_braces_escaped_in_template(self):
        with patch("agentnexus.prompts.load_prompt", return_value="{{literal}} {date}"):
            result = prompts.format_prompt("test")
        assert "{literal}" in result

    def test_unicode_injection(self):
        with patch("agentnexus.prompts.load_prompt", return_value="{question}"):
            result = prompts.format_prompt("test", question="中文测试 🎉")
        assert "中文测试" in result
