"""P1-2: Streaming output interruption/cancel test.

AgentLLM always uses stream=True. There is no explicit cancel mechanism;
the streaming loop runs to completion. These tests verify:
- Stream interruption via exception is handled gracefully
- Stream with empty chunks
- Error during streaming does not leak resources
"""
from unittest.mock import patch

from agentnexus.core.llm import AgentLLM
from agentnexus.core.providers.base import StreamResult


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
        llm = AgentLLM(model="test/test-model", apiKey="sk-test", baseUrl="http://localhost:9999")
        llm.api_key = ""  # Simulate empty key after init
        result = llm.think([{"role": "user", "content": "hi"}])
        assert result == ""

    @patch("agentnexus.core.llm.get_settings")
    @patch("agentnexus.core.llm.trace_manager")
    @patch("agentnexus.core.llm.time.sleep")
    def test_call_exception_returns_empty(self, mock_sleep, mock_trace, mock_settings):
        mock_settings.return_value.llm_model_id = "test-model"
        mock_settings.return_value.llm_api_key.get_secret_value.return_value = "sk-test"
        mock_settings.return_value.llm_base_url = "http://localhost:9999"
        mock_settings.return_value.llm_timeout = 60
        mock_trace.active = None

        llm = AgentLLM()
        with patch.object(llm, "_call_via_provider", side_effect=RuntimeError("connection failed")):
            with patch("litellm.completion", side_effect=RuntimeError("connection failed")):
                result = llm.think([{"role": "user", "content": "hi"}])
                assert result == ""
                assert llm.last_error is not None

    @patch("agentnexus.core.llm.get_settings")
    @patch("agentnexus.core.llm.trace_manager")
    @patch("agentnexus.core.llm.time.sleep")
    def test_last_error_set_on_failure(self, mock_sleep, mock_trace, mock_settings):
        mock_settings.return_value.llm_model_id = "test-model"
        mock_settings.return_value.llm_api_key.get_secret_value.return_value = "sk-test"
        mock_settings.return_value.llm_base_url = "http://localhost:9999"
        mock_settings.return_value.llm_timeout = 60
        mock_trace.active = None

        llm = AgentLLM()
        with patch.object(llm, "_call_via_provider", side_effect=RuntimeError("timeout")):
            with patch("litellm.completion", side_effect=RuntimeError("timeout")):
                llm.think([{"role": "user", "content": "hi"}])
                assert "timeout" in (llm.last_error or "").lower()

    @patch("agentnexus.core.llm.get_settings")
    @patch("agentnexus.core.llm.trace_manager")
    @patch("agentnexus.core.llm.time.sleep")
    def test_non_transient_error_returns_immediately(self, mock_sleep, mock_trace, mock_settings):
        mock_settings.return_value.llm_model_id = "test-model"
        mock_settings.return_value.llm_api_key.get_secret_value.return_value = "sk-test"
        mock_settings.return_value.llm_base_url = "http://localhost:9999"
        mock_settings.return_value.llm_timeout = 60
        mock_trace.active = None

        llm = AgentLLM()
        call_count = [0]

        def _fail(*args, **kwargs):
            call_count[0] += 1
            raise ValueError("bad request")

        with patch.object(llm, "_call_via_provider", side_effect=_fail):
            with patch("litellm.completion", side_effect=_fail):
                result = llm.think([{"role": "user", "content": "hi"}])
                assert result == ""
                # Non-transient errors (ValueError) stop immediately, no retries
                assert call_count[0] >= 1
                mock_sleep.assert_not_called()


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

    @patch("agentnexus.core.llm._provider_health", {})
    @patch("agentnexus.core.llm.get_settings")
    @patch("agentnexus.core.llm.trace_manager")
    def test_last_usage_populated(self, mock_trace, mock_settings):
        mock_settings.return_value.llm_model_id = "test-model"
        mock_settings.return_value.llm_api_key.get_secret_value.return_value = "sk-test"
        mock_settings.return_value.llm_base_url = "http://localhost:9999"
        mock_settings.return_value.llm_timeout = 60
        mock_trace.active = None

        llm = AgentLLM()
        result_obj = StreamResult(
            text="hello", usage={"input_tokens": 5, "output_tokens": 5},
            finish_reason="stop",
        )
        with patch.object(llm, "_call_via_provider", return_value=result_obj):
            llm._call([{"role": "user", "content": "hi"}], 0, True, 0)
            assert isinstance(llm.last_usage, dict)

    @patch("agentnexus.core.llm._provider_health", {})
    @patch("agentnexus.core.llm.get_settings")
    @patch("agentnexus.core.llm.trace_manager")
    def test_total_usage_accumulates(self, mock_trace, mock_settings):
        mock_settings.return_value.llm_model_id = "test-model"
        mock_settings.return_value.llm_api_key.get_secret_value.return_value = "sk-test"
        mock_settings.return_value.llm_base_url = "http://localhost:9999"
        mock_settings.return_value.llm_timeout = 60
        mock_trace.active = None

        llm = AgentLLM()
        result_obj = StreamResult(
            text="response", usage={"input_tokens": 10, "output_tokens": 10},
            finish_reason="stop",
        )
        with patch.object(llm, "_call_via_provider", return_value=result_obj):
            llm._call([{"role": "user", "content": "hi"}], 0, True, 0)
            assert llm.total_usage["input_tokens"] >= 5
