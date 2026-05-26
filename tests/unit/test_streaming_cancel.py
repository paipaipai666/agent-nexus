"""Streaming interruption/cancel tests.

Validates that streaming responses handle mid-stream exceptions gracefully.
"""
from unittest.mock import MagicMock, patch

from agentnexus.core.llm import AgentLLM


class _MockStreamChunk:
    def __init__(self, content="", finish_reason=None, usage=None):
        self.choices = [
            MagicMock(
                delta=MagicMock(
                    content=content,
                    tool_calls=[],
                    reasoning_content=None,
                ),
                finish_reason=finish_reason,
            )
        ]
        self.usage = usage


def _chunk_iter(*chunks):
    for chunk in chunks:
        yield chunk


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

        chunks = [
            _MockStreamChunk(content="", finish_reason=None),
            _MockStreamChunk(content="Hello", finish_reason=None),
            _MockStreamChunk(content="", finish_reason=None),
            _MockStreamChunk(content=" world", finish_reason="stop"),
        ]

        with patch("litellm.completion", return_value=_chunk_iter(*chunks)):
            llm = AgentLLM()
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

        chunks = [
            _MockStreamChunk(content="Hello ", finish_reason=None),
            _MockStreamChunk(content="world", finish_reason="stop"),
        ]

        with patch("litellm.completion", return_value=_chunk_iter(*chunks)):
            llm = AgentLLM()
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

        with patch("litellm.completion", side_effect=ValueError("bad request")):
            llm = AgentLLM()
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

        with patch("litellm.completion", side_effect=RuntimeError("Connection failed")):
            llm = AgentLLM()
            result = llm.think([{"role": "user", "content": "hi"}])
            assert result == ""
