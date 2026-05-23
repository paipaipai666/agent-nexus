"""Tests for agentnexus.core.judge_llm."""

from unittest.mock import patch

import pytest

from agentnexus.core.judge_llm import _judge_llm, get_judge_llm


class TestGetJudgeLLM:
    def teardown_method(self):
        import agentnexus.core.judge_llm as m
        m._judge_llm = None

    @patch("agentnexus.core.judge_llm.get_settings")
    @patch("agentnexus.core.judge_llm.AgentLLM")
    def test_creates_llm_with_judge_key(self, MockLLM, mock_settings):
        mock_settings.return_value.judge_api_key.get_secret_value.return_value = "judge-key"
        mock_settings.return_value.llm_api_key.get_secret_value.return_value = "gen-key"
        mock_settings.return_value.judge_model_id = "glm-4"
        mock_settings.return_value.judge_base_url = "https://judge.example.com"

        result = get_judge_llm()

        MockLLM.assert_called_once_with(
            model="glm-4",
            apiKey="judge-key",
            baseUrl="https://judge.example.com",
        )
        assert result is MockLLM.return_value

    @patch("agentnexus.core.judge_llm.get_settings")
    @patch("agentnexus.core.judge_llm.AgentLLM")
    def test_falls_back_to_gen_key_when_judge_key_empty(self, MockLLM, mock_settings):
        mock_settings.return_value.judge_api_key.get_secret_value.return_value = ""
        mock_settings.return_value.llm_api_key.get_secret_value.return_value = "gen-key"
        mock_settings.return_value.judge_model_id = "glm-4"
        mock_settings.return_value.judge_base_url = ""

        result = get_judge_llm()

        MockLLM.assert_called_once_with(
            model="glm-4",
            apiKey="gen-key",
            baseUrl="",
        )
        assert result is MockLLM.return_value

    @patch("agentnexus.core.judge_llm.get_settings")
    @patch("agentnexus.core.judge_llm.AgentLLM")
    def test_singleton_returns_same_instance(self, MockLLM, mock_settings):
        mock_settings.return_value.judge_api_key.get_secret_value.return_value = "judge-key"
        mock_settings.return_value.llm_api_key.get_secret_value.return_value = "gen-key"
        mock_settings.return_value.judge_model_id = "glm-4"
        mock_settings.return_value.judge_base_url = ""

        first = get_judge_llm()
        second = get_judge_llm()

        MockLLM.assert_called_once()
        assert first is second
