"""LLM structured output schema validation tests.

Validates that LLM responses conform to expected JSON schemas,
including nested objects, arrays, and required fields.
"""
import json
from unittest.mock import MagicMock, patch

import pytest

from agentnexus.core.llm import AgentLLM


class TestLLMStructuredOutputSchema:
    """Validate that LLM responses can be parsed against JSON schemas."""

    @patch("agentnexus.core.llm.get_settings")
    @patch("agentnexus.core.llm.trace_manager")
    def test_json_mode_response_parsable(self, mock_trace, mock_settings):
        mock_settings.return_value.llm_model_id = "test-model"
        mock_settings.return_value.llm_api_key.get_secret_value.return_value = "sk-test"
        mock_settings.return_value.llm_base_url = "http://localhost:9999"
        mock_settings.return_value.llm_timeout = 60
        mock_trace.active = None

        schema = {
            "type": "object",
            "properties": {
                "thought": {"type": "string"},
                "action": {"type": "string"},
            },
            "required": ["thought", "action"],
        }

        chunk = MagicMock()
        delta = MagicMock()
        delta.content = '{"thought": "I need to search", "action": "web_search"}'
        delta.tool_calls = []
        delta.reasoning_content = None
        chunk.choices = [MagicMock(delta=delta, finish_reason="stop")]
        chunk.usage = None

        with patch("litellm.completion", return_value=[chunk]):
            llm = AgentLLM()
            result = llm._call(
                [{"role": "user", "content": "test"}],
                0, True, 0,
                response_format={"type": "json_object"},
            )
            parsed = json.loads(result)
            assert "thought" in parsed
            assert "action" in parsed

    @patch("agentnexus.core.llm.get_settings")
    @patch("agentnexus.core.llm.trace_manager")
    def test_nested_schema_parsable(self, mock_trace, mock_settings):
        mock_settings.return_value.llm_model_id = "test-model"
        mock_settings.return_value.llm_api_key.get_secret_value.return_value = "sk-test"
        mock_settings.return_value.llm_base_url = "http://localhost:9999"
        mock_settings.return_value.llm_timeout = 60
        mock_trace.active = None

        response = '{"user": {"name": "Alice", "age": 30}, "items": [1, 2, 3]}'
        chunk = MagicMock()
        delta = MagicMock()
        delta.content = response
        delta.tool_calls = []
        delta.reasoning_content = None
        chunk.choices = [MagicMock(delta=delta, finish_reason="stop")]
        chunk.usage = None

        with patch("litellm.completion", return_value=[chunk]):
            llm = AgentLLM()
            result = llm._call(
                [{"role": "user", "content": "test"}],
                0, True, 0,
                response_format={"type": "json_object"},
            )
            parsed = json.loads(result)
            assert parsed["user"]["name"] == "Alice"
            assert parsed["items"] == [1, 2, 3]

    @patch("agentnexus.core.llm.get_settings")
    @patch("agentnexus.core.llm.trace_manager")
    def test_schema_with_array_of_objects(self, mock_trace, mock_settings):
        mock_settings.return_value.llm_model_id = "test-model"
        mock_settings.return_value.llm_api_key.get_secret_value.return_value = "sk-test"
        mock_settings.return_value.llm_base_url = "http://localhost:9999"
        mock_settings.return_value.llm_timeout = 60
        mock_trace.active = None

        response = '{"tools": [{"name": "search", "params": {"q": "test"}}]}'
        chunk = MagicMock()
        delta = MagicMock()
        delta.content = response
        delta.tool_calls = []
        delta.reasoning_content = None
        chunk.choices = [MagicMock(delta=delta, finish_reason="stop")]
        chunk.usage = None

        with patch("litellm.completion", return_value=[chunk]):
            llm = AgentLLM()
            result = llm._call(
                [{"role": "user", "content": "test"}],
                0, True, 0,
                response_format={"type": "json_object"},
            )
            parsed = json.loads(result)
            assert len(parsed["tools"]) == 1
            assert parsed["tools"][0]["name"] == "search"

    @patch("agentnexus.core.llm.get_settings")
    @patch("agentnexus.core.llm.trace_manager")
    def test_empty_json_response(self, mock_trace, mock_settings):
        mock_settings.return_value.llm_model_id = "test-model"
        mock_settings.return_value.llm_api_key.get_secret_value.return_value = "sk-test"
        mock_settings.return_value.llm_base_url = "http://localhost:9999"
        mock_settings.return_value.llm_timeout = 60
        mock_trace.active = None

        chunk = MagicMock()
        delta = MagicMock()
        delta.content = "{}"
        delta.tool_calls = []
        delta.reasoning_content = None
        chunk.choices = [MagicMock(delta=delta, finish_reason="stop")]
        chunk.usage = None

        with patch("litellm.completion", return_value=[chunk]):
            llm = AgentLLM()
            result = llm._call(
                [{"role": "user", "content": "test"}],
                0, True, 0,
                response_format={"type": "json_object"},
            )
            parsed = json.loads(result)
            assert parsed == {}

    @patch("agentnexus.core.llm.get_settings")
    @patch("agentnexus.core.llm.trace_manager")
    def test_malformed_json_returns_raw(self, mock_trace, mock_settings):
        mock_settings.return_value.llm_model_id = "test-model"
        mock_settings.return_value.llm_api_key.get_secret_value.return_value = "sk-test"
        mock_settings.return_value.llm_base_url = "http://localhost:9999"
        mock_settings.return_value.llm_timeout = 60
        mock_trace.active = None

        chunk = MagicMock()
        delta = MagicMock()
        delta.content = '{"incomplete": '
        delta.tool_calls = []
        delta.reasoning_content = None
        chunk.choices = [MagicMock(delta=delta, finish_reason="stop")]
        chunk.usage = None

        with patch("litellm.completion", return_value=[chunk]):
            llm = AgentLLM()
            result = llm._call(
                [{"role": "user", "content": "test"}],
                0, True, 0,
                response_format={"type": "json_object"},
            )
            assert result == '{"incomplete": '
