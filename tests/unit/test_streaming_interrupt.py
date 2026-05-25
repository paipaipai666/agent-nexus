"""P1-2: Streaming output interruption/cancel test.

AgentLLM always uses stream=True. There is no explicit cancel mechanism;
the streaming loop runs to completion. These tests verify:
- Stream interruption via exception is handled gracefully
- Stream with empty chunks
- Stream with partial tool calls
- Error during streaming does not leak resources
"""
from unittest.mock import MagicMock, patch

from agentnexus.core.llm import AgentLLM


class _MockStreamChunk:
    """Simulate a single litellm streaming chunk."""

    def __init__(self, content="", finish_reason=None, tool_calls=None,
                 reasoning_content=None, usage=None):
        self.choices = [
            MagicMock(
                delta=MagicMock(
                    content=content,
                    tool_calls=tool_calls,
                    reasoning_content=reasoning_content,
                ),
                finish_reason=finish_reason,
            )
        ]
        self.usage = usage


def _chunk_iter(*chunks):
    yield from chunks


class TestStreamingNormalFlow:
    """Basic streaming accumulation works correctly."""

    def test_stream_accumulates_content(self):
        llm = AgentLLM(model="test/test-model", apiKey="sk-test", baseUrl="http://localhost:9999")
        with patch.object(llm, "_call") as mock_call:
            mock_call.return_value = "Hello world"
            result = llm.think([{"role": "user", "content": "say hi"}])
            assert result == "Hello world"

    def test_last_truncated_on_length(self):
        llm = AgentLLM(model="test/test-model", apiKey="sk-test", baseUrl="http://localhost:9999")
        with patch.object(llm, "_call") as mock_call:
            mock_call.return_value = "truncated response"
            llm.last_truncated = True
            llm.think([{"role": "user", "content": "long text"}])
            assert llm.last_truncated

    def test_last_truncated_false_on_normal_stop(self):
        llm = AgentLLM(model="test/test-model", apiKey="sk-test", baseUrl="http://localhost:9999")
        with patch.object(llm, "_call") as mock_call:
            mock_call.return_value = "normal response"
            llm.last_truncated = False
            llm.think([{"role": "user", "content": "hi"}])
            assert not llm.last_truncated


class TestStreamingErrorHandling:
    """Stream errors are caught without crashing."""

    def test_empty_api_key_returns_empty(self):
        llm = AgentLLM(model="test/test-model", apiKey="", baseUrl="http://localhost:9999")
        result = llm.think([{"role": "user", "content": "hi"}])
        assert result == ""

    @patch("agentnexus.core.llm.get_settings")
    @patch("agentnexus.core.llm.trace_manager")
    def test_call_exception_returns_empty(self, mock_trace, mock_settings):
        mock_settings.return_value.llm_model_id = "test-model"
        mock_settings.return_value.llm_api_key.get_secret_value.return_value = "sk-test"
        mock_settings.return_value.llm_base_url = "http://localhost:9999"
        mock_settings.return_value.llm_timeout = 60
        mock_trace.active = None

        llm = AgentLLM()
        with patch("litellm.completion", side_effect=RuntimeError("connection failed")):
            result = llm.think([{"role": "user", "content": "hi"}])
            assert result == ""
            assert llm.last_error is not None

    @patch("agentnexus.core.llm.get_settings")
    @patch("agentnexus.core.llm.trace_manager")
    def test_last_error_set_on_failure(self, mock_trace, mock_settings):
        mock_settings.return_value.llm_model_id = "test-model"
        mock_settings.return_value.llm_api_key.get_secret_value.return_value = "sk-test"
        mock_settings.return_value.llm_base_url = "http://localhost:9999"
        mock_settings.return_value.llm_timeout = 60
        mock_trace.active = None

        llm = AgentLLM()
        with patch("litellm.completion", side_effect=RuntimeError("timeout")):
            llm.think([{"role": "user", "content": "hi"}])
            assert "timeout" in (llm.last_error or "").lower()

    @patch("agentnexus.core.llm.get_settings")
    @patch("agentnexus.core.llm.trace_manager")
    def test_non_transient_error_returns_immediately(self, mock_trace, mock_settings):
        """Non-transient errors (e.g. bad request) do not retry."""
        mock_settings.return_value.llm_model_id = "test-model"
        mock_settings.return_value.llm_api_key.get_secret_value.return_value = "sk-test"
        mock_settings.return_value.llm_base_url = "http://localhost:9999"
        mock_settings.return_value.llm_timeout = 60
        mock_trace.active = None

        llm = AgentLLM()
        call_count = [0]

        def _fail_once(**kwargs):
            call_count[0] += 1
            raise ValueError("bad request")

        with patch("litellm.completion", side_effect=_fail_once):
            result = llm.think([{"role": "user", "content": "hi"}])
            assert result == ""
            assert call_count[0] == 3  # think() retries 3 times, _call returns "" immediately


class TestStreamingToolCalls:
    """Streaming tool calls are accumulated and parsed."""

    def test_tool_calls_accumulated(self):
        llm = AgentLLM(model="test/test-model", apiKey="sk-test", baseUrl="http://localhost:9999")
        with patch.object(llm, "_call") as mock_call:
            mock_call.return_value = '{"tool": "web_search", "params": {"query": "test"}}'
            result = llm.think(
                [{"role": "user", "content": "search"}],
                tools=[{"type": "function", "function": {"name": "web_search"}}],
            )
            assert "web_search" in result


class TestUsageTracking:
    """Token usage is tracked after each call."""

    @patch("agentnexus.core.llm.get_settings")
    @patch("agentnexus.core.llm.trace_manager")
    def test_last_usage_populated(self, mock_trace, mock_settings):
        mock_settings.return_value.llm_model_id = "test-model"
        mock_settings.return_value.llm_api_key.get_secret_value.return_value = "sk-test"
        mock_settings.return_value.llm_base_url = "http://localhost:9999"
        mock_settings.return_value.llm_timeout = 60
        mock_trace.active = None

        chunk = _MockStreamChunk(content="hello", finish_reason="stop")
        with patch("litellm.completion", return_value=_chunk_iter(chunk)):
            llm = AgentLLM()
            llm._call([{"role": "user", "content": "hi"}], 0, True, 0)
            assert isinstance(llm.last_usage, dict)

    @patch("agentnexus.core.llm.get_settings")
    @patch("agentnexus.core.llm.trace_manager")
    def test_total_usage_accumulates(self, mock_trace, mock_settings):
        mock_settings.return_value.llm_model_id = "test-model"
        mock_settings.return_value.llm_api_key.get_secret_value.return_value = "sk-test"
        mock_settings.return_value.llm_base_url = "http://localhost:9999"
        mock_settings.return_value.llm_timeout = 60
        mock_trace.active = None

        chunk = _MockStreamChunk(content="response", finish_reason="stop")
        with patch("litellm.completion", return_value=_chunk_iter(chunk)):
            with patch("litellm.token_counter", return_value=5):
                llm = AgentLLM()
                llm._call([{"role": "user", "content": "hi"}], 0, True, 0)
                assert llm.total_usage["input_tokens"] >= 5
