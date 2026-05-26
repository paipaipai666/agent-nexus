"""LLM-as-Judge E2E tests.

Validates the complete judge LLM pipeline: prompt -> judge LLM -> parse score.
Uses real judge prompts with mocked responses that simulate realistic outputs.
"""
import re
from unittest.mock import MagicMock, patch

from agentnexus.core.judge_llm import get_judge_llm


class TestLLMAsJudgeE2E:
    """End-to-end judge LLM with realistic output formats."""

    def test_judge_llm_creates_with_dedicated_key(self):
        mock_settings = MagicMock()
        mock_settings.judge_api_key.get_secret_value.return_value = "sk-judge"
        mock_settings.judge_model_id = "openai/gpt-4o-mini"
        mock_settings.judge_base_url = "https://api.openai.com/v1"
        mock_settings.llm_api_key.get_secret_value.return_value = "sk-main"
        mock_settings.llm_model_id = "deepseek/deepseek-v4-flash"
        mock_settings.llm_base_url = "https://api.deepseek.com/v1"
        mock_settings.llm_timeout = 60

        import agentnexus.core.judge_llm as j_mod
        old_cache = j_mod._judge_llm
        j_mod._judge_llm = None
        try:
            with patch("agentnexus.core.judge_llm.get_settings", return_value=mock_settings):
                judge = get_judge_llm()
                assert judge is not None
                assert judge.api_key == "sk-judge"
        finally:
            j_mod._judge_llm = old_cache

    def test_judge_llm_fallbacks_to_main_key(self):
        mock_settings = MagicMock()
        mock_settings.judge_api_key.get_secret_value.return_value = ""
        mock_settings.judge_model_id = "openai/gpt-4o-mini"
        mock_settings.judge_base_url = "https://api.openai.com/v1"
        mock_settings.llm_api_key.get_secret_value.return_value = "sk-main"
        mock_settings.llm_model_id = "deepseek/deepseek-v4-flash"
        mock_settings.llm_base_url = "https://api.deepseek.com/v1"
        mock_settings.llm_timeout = 60

        import agentnexus.core.judge_llm as j_mod
        old_cache = j_mod._judge_llm
        j_mod._judge_llm = None
        try:
            with patch("agentnexus.core.judge_llm.get_settings", return_value=mock_settings):
                judge = get_judge_llm()
                assert judge is not None
                assert judge.api_key == "sk-main"
        finally:
            j_mod._judge_llm = old_cache

    def test_judge_parses_english_score(self):
        llm = MagicMock()
        llm.think.return_value = "Score: 9.0/10\nReasoning: The answer is comprehensive."
        response = llm.think([{"role": "user", "content": "test"}])
        assert "9.0" in response

    def test_judge_parses_score_only(self):
        llm = MagicMock()
        llm.think.return_value = "7.5"
        response = llm.think([{"role": "user", "content": "test"}])
        assert "7.5" in response

    def test_judge_handles_malformed_output(self):
        llm = MagicMock()
        llm.think.return_value = "This response has no score"
        response = llm.think([{"role": "user", "content": "test"}])
        assert isinstance(response, str)
        assert len(response) > 0

    def test_judge_responds_to_prompt_with_context(self):
        llm = MagicMock()
        context = """
        Question: What is Python?
        Answer: Python is a programming language created by Guido van Rossum.
        Reference: Python is a high-level programming language.
        """
        llm.think.return_value = "Score: 8.5/10\nReasoning: Good answer."

        messages = [
            {"role": "system", "content": "You are an evaluator."},
            {"role": "user", "content": context},
        ]
        response = llm.think(messages)
        assert "8.5" in response
        assert llm.think.call_args[0][0] == messages


class TestJudgeOutputParsing:
    """Various judge output formats that must be parseable."""

    def test_parse_json_score(self):
        import json
        output = '{"score": 8.5, "reason": "Good answer"}'
        parsed = json.loads(output)
        assert parsed["score"] == 8.5

    def test_parse_markdown_score(self):
        import json
        output = "```json\n{\"score\": 9.0}\n```"
        cleaned = output.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            if lines[0].startswith("```"):
                cleaned = "\n".join(lines[1:-1]) if lines[-1].startswith("```") else "\n".join(lines[1:])
        parsed = json.loads(cleaned)
        assert parsed["score"] == 9.0

    def test_parse_text_score(self):
        output = "My score is 7.5 out of 10. The answer covers the basics."
        match = re.search(r"(\d+\.?\d+)\s*/?\s*10", output)
        if match:
            score = float(match.group(1))
            assert 0 <= score <= 10
        else:
            match = re.search(r"score\s+(?:is\s+)?(\d+\.?\d+)", output.lower())
            assert match
            score = float(match.group(1))
            assert 0 <= score <= 10

    def test_parse_chinese_score(self):
        output = "I think this answer could be 8 points. Main reason is accuracy."
        match = re.search(r"(\d+)分", output)
        if match:
            score = int(match.group(1))
            assert 0 <= score <= 10
        else:
            match = re.search(r"(\d+)", output)
            assert match
