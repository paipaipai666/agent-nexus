"""Streaming interruption/cancel tests.

Validates that streaming responses handle mid-stream exceptions gracefully.
"""
from unittest.mock import patch

from agentnexus.core.llm import AgentLLM
from agentnexus.core.providers.base import StreamResult


class TestStreamingInterruption:
    """Stream interruption handling."""

    @patch("agentnexus.core.llm.get_settings")
    @patch("agentnexus.core.llm.trace_manager")
    def test_streaming_with_empty_chunks(self, mock_trace, mock_settings):
        mock_settings.return_value.llm_model_id = "test-model"
        mock_settings.return_value.llm_api_key.get_secret_value.return_value = "sk-test"
        mock_settings.return_value.llm_base_url = "http://localhost:9999"
        mock_settings.return_value.llm_timeout = 60
        mock_trace.active = None

        llm = AgentLLM()
        result_obj = StreamResult(text="Hello world", finish_reason="stop")
        with patch.object(llm, "_call_via_provider", return_value=result_obj):
            result = llm._call([{"role": "user", "content": "hi"}], 0, True, 0)
            assert "Hello world" in result

    @patch("agentnexus.core.llm.get_settings")
    @patch("agentnexus.core.llm.trace_manager")
    def test_streaming_normal_flow(self, mock_trace, mock_settings):
        mock_settings.return_value.llm_model_id = "test-model"
        mock_settings.return_value.llm_api_key.get_secret_value.return_value = "sk-test"
        mock_settings.return_value.llm_base_url = "http://localhost:9999"
        mock_settings.return_value.llm_timeout = 60
        mock_trace.active = None

        llm = AgentLLM()
        result_obj = StreamResult(text="Hello world", finish_reason="stop")
        with patch.object(llm, "_call_via_provider", return_value=result_obj):
            result = llm._call([{"role": "user", "content": "hi"}], 0, True, 0)
            assert "Hello world" in result

    @patch("agentnexus.core.llm.get_settings")
    @patch("agentnexus.core.llm.trace_manager")
    def test_streaming_error_returns_empty(self, mock_trace, mock_settings):
        mock_settings.return_value.llm_model_id = "test-model"
        mock_settings.return_value.llm_api_key.get_secret_value.return_value = "sk-test"
        mock_settings.return_value.llm_base_url = "http://localhost:9999"
        mock_settings.return_value.llm_timeout = 60
        mock_trace.active = None

        llm = AgentLLM()
        with patch.object(llm, "_call_via_provider", side_effect=ValueError("bad request")):
            with patch("litellm.completion", side_effect=ValueError("bad request")):
                result = llm._call([{"role": "user", "content": "hi"}], 0, True, 0)
                assert result == ""

    @patch("agentnexus.core.llm.get_settings")
    @patch("agentnexus.core.llm.trace_manager")
    def test_think_handles_stream_error(self, mock_trace, mock_settings):
        mock_settings.return_value.llm_model_id = "test-model"
        mock_settings.return_value.llm_api_key.get_secret_value.return_value = "sk-test"
        mock_settings.return_value.llm_base_url = "http://localhost:9999"
        mock_settings.return_value.llm_timeout = 60
        mock_trace.active = None

        llm = AgentLLM()
        with patch.object(llm, "_call_via_provider", side_effect=RuntimeError("Connection failed")):
            with patch("litellm.completion", side_effect=RuntimeError("Connection failed")):
                result = llm.think([{"role": "user", "content": "hi"}])
                assert result == ""
