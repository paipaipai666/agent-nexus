"""Tests for agentnexus.core.judge_llm."""

from unittest.mock import patch

from pydantic import SecretStr

from agentnexus.core.judge_llm import get_judge_llm


class TestGetJudgeLLM:
    def setup_method(self):
        import agentnexus.core.judge_llm as m
        m._judge_llm = None

    def teardown_method(self):
        import agentnexus.core.judge_llm as m
        m._judge_llm = None

    @patch("agentnexus.core.judge_llm.get_settings")
    @patch("agentnexus.core.judge_llm.AgentLLM")
    def test_creates_llm_with_judge_key(self, MockLLM, mock_settings):
        mock_settings.return_value.get_judge_profile.return_value = (
            "glm-4", "https://judge.example.com", SecretStr("judge-key"), 60,
        )

        result = get_judge_llm()

        MockLLM.assert_called_once_with(
            model="glm-4",
            apiKey="judge-key",
            baseUrl="https://judge.example.com",
            timeout=60,
        )
        assert result is MockLLM.return_value

    @patch("agentnexus.core.judge_llm.get_settings")
    @patch("agentnexus.core.judge_llm.AgentLLM")
    def test_falls_back_to_gen_key_when_judge_key_empty(self, MockLLM, mock_settings):
        mock_settings.return_value.get_judge_profile.return_value = (
            "glm-4", "", SecretStr("gen-key"), 60,
        )

        result = get_judge_llm()

        MockLLM.assert_called_once_with(
            model="glm-4",
            apiKey="gen-key",
            baseUrl="",
            timeout=60,
        )
        assert result is MockLLM.return_value

    @patch("agentnexus.core.judge_llm.get_settings")
    @patch("agentnexus.core.judge_llm.AgentLLM")
    def test_singleton_returns_same_instance(self, MockLLM, mock_settings):
        mock_settings.return_value.get_judge_profile.return_value = (
            "glm-4", "", SecretStr("judge-key"), 60,
        )

        first = get_judge_llm()
        second = get_judge_llm()

        MockLLM.assert_called_once()
        assert first is second
