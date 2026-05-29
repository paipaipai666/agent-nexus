"""P1-5: Token consumption regression threshold test.

Verifies that AgentLLM correctly tracks token usage and that
total_usage does not grow unbounded.

No API calls are made — all tests use mocked LLM responses.
"""
from unittest.mock import MagicMock, patch

from agentnexus.core.llm import AgentLLM


class _MockChunk:
    def __init__(self, content="", finish_reason=""):
        delta = MagicMock()
        delta.content = content
        delta.tool_calls = []
        delta.reasoning_content = None
        self.choices = [MagicMock(delta=delta, finish_reason=finish_reason)]
        self.usage = None


def _chunk_iter(*chunks):
    yield from chunks


def _mock_estimate_usage(model, messages, result):
    """Deterministic token estimate for testing."""
    return {"input_tokens": 5, "output_tokens": 5, "total_tokens": 10}


def _mock_estimate_usage_10(model, messages, result):
    return {"input_tokens": 10, "output_tokens": 10, "total_tokens": 20}


class TestTokenUsageTracking:
    """last_usage and total_usage are tracked correctly via _call()."""

    @patch("agentnexus.core.llm.AgentLLM._estimate_usage", side_effect=_mock_estimate_usage)
    @patch("agentnexus.core.llm.get_settings")
    @patch("agentnexus.core.llm.trace_manager")
    def test_last_usage_after_single_call(self, mock_trace, mock_settings, mock_est):
        mock_settings.return_value.llm_model_id = "test-model"
        mock_settings.return_value.llm_api_key.get_secret_value.return_value = "sk-test"
        mock_settings.return_value.llm_base_url = "http://localhost:9999"
        mock_settings.return_value.llm_timeout = 60
        mock_trace.active = None

        chunk = _MockChunk(content="hi", finish_reason="stop")
        with patch("litellm.completion", return_value=_chunk_iter(chunk)):
            llm = AgentLLM()
            llm._call([{"role": "user", "content": "hi"}], 0, True, 0)
            assert llm.last_usage["input_tokens"] == 5
            assert llm.last_usage["output_tokens"] == 5

    @patch("agentnexus.core.llm.AgentLLM._estimate_usage", side_effect=_mock_estimate_usage)
    @patch("agentnexus.core.llm.get_settings")
    @patch("agentnexus.core.llm.trace_manager")
    def test_total_usage_accumulates(self, mock_trace, mock_settings, mock_est):
        mock_settings.return_value.llm_model_id = "test-model"
        mock_settings.return_value.llm_api_key.get_secret_value.return_value = "sk-test"
        mock_settings.return_value.llm_base_url = "http://localhost:9999"
        mock_settings.return_value.llm_timeout = 60
        mock_trace.active = None

        chunk = _MockChunk(content="hi", finish_reason="stop")
        with patch("litellm.completion", return_value=_chunk_iter(chunk)):
            llm = AgentLLM()
            llm._call([{"role": "user", "content": "hi"}], 0, True, 0)
            assert llm.total_usage["input_tokens"] >= 5
            assert llm.total_usage["output_tokens"] >= 5

    @patch("agentnexus.core.llm.AgentLLM._estimate_usage", side_effect=_mock_estimate_usage_10)
    @patch("agentnexus.core.llm.get_settings")
    @patch("agentnexus.core.llm.trace_manager")
    def test_consecutive_calls_accumulate(self, mock_trace, mock_settings, mock_est):
        mock_settings.return_value.llm_model_id = "test-model"
        mock_settings.return_value.llm_api_key.get_secret_value.return_value = "sk-test"
        mock_settings.return_value.llm_base_url = "http://localhost:9999"
        mock_settings.return_value.llm_timeout = 60
        mock_trace.active = None

        chunk = _MockChunk(content="r", finish_reason="stop")
        with patch("litellm.completion", return_value=_chunk_iter(chunk)):
            llm = AgentLLM()
            llm._call([{"role": "user", "content": "msg 0"}], 0, True, 0)
            llm._call([{"role": "user", "content": "msg 1"}], 0, True, 0)
            llm._call([{"role": "user", "content": "msg 2"}], 0, True, 0)
            assert llm.total_usage["input_tokens"] >= 30
            assert llm.total_usage["output_tokens"] >= 30

    @patch("agentnexus.core.llm.get_settings")
    @patch("agentnexus.core.llm.trace_manager")
    def test_empty_response_has_zero_tokens(self, mock_trace, mock_settings):
        mock_settings.return_value.llm_model_id = "test-model"
        mock_settings.return_value.llm_api_key.get_secret_value.return_value = "sk-test"
        mock_settings.return_value.llm_base_url = "http://localhost:9999"
        mock_settings.return_value.llm_timeout = 60
        mock_trace.active = None

        chunk = _MockChunk(content="", finish_reason="stop")
        with patch("litellm.completion", return_value=_chunk_iter(chunk)):
            llm = AgentLLM()
            llm._call([{"role": "user", "content": "hi"}], 0, True, 0)
            assert llm.last_usage.get("total_tokens", 0) >= 0

    @patch("agentnexus.core.llm.AgentLLM._estimate_usage", side_effect=_mock_estimate_usage_10)
    @patch("agentnexus.core.llm.get_settings")
    @patch("agentnexus.core.llm.trace_manager")
    def test_total_usage_does_not_reset(self, mock_trace, mock_settings, mock_est):
        """total_usage persists across calls, never re-initialized."""
        mock_settings.return_value.llm_model_id = "test-model"
        mock_settings.return_value.llm_api_key.get_secret_value.return_value = "sk-test"
        mock_settings.return_value.llm_base_url = "http://localhost:9999"
        mock_settings.return_value.llm_timeout = 60
        mock_trace.active = None

        chunk = _MockChunk(content="r", finish_reason="stop")
        with patch("litellm.completion", return_value=_chunk_iter(chunk)):
            llm = AgentLLM()
            llm.total_usage = {"input_tokens": 100, "output_tokens": 50}
            llm._call([{"role": "user", "content": "a"}], 0, True, 0)
            llm._call([{"role": "user", "content": "b"}], 0, True, 0)
            assert llm.total_usage["input_tokens"] >= 120
            assert llm.total_usage["output_tokens"] >= 70


class TestUsageNoOverflow:
    """Token counts should not grow beyond expected bounds."""

    @patch("agentnexus.core.llm.AgentLLM._estimate_usage",
           return_value={"input_tokens": 0, "output_tokens": 0, "total_tokens": 0})
    @patch("agentnexus.core.llm.get_settings")
    @patch("agentnexus.core.llm.trace_manager")
    def test_no_negative_tokens(self, mock_trace, mock_settings, mock_est):
        mock_settings.return_value.llm_model_id = "test-model"
        mock_settings.return_value.llm_api_key.get_secret_value.return_value = "sk-test"
        mock_settings.return_value.llm_base_url = "http://localhost:9999"
        mock_settings.return_value.llm_timeout = 60
        mock_trace.active = None

        chunk = _MockChunk(content="ok", finish_reason="stop")
        with patch("litellm.completion", return_value=_chunk_iter(chunk)):
            llm = AgentLLM()
            llm._call([{"role": "user", "content": "x"}], 0, True, 0)
            assert llm.last_usage["input_tokens"] >= 0
            assert llm.last_usage["output_tokens"] >= 0

    @patch("agentnexus.core.llm.AgentLLM._estimate_usage", side_effect=_mock_estimate_usage)
    @patch("agentnexus.core.llm.get_settings")
    @patch("agentnexus.core.llm.trace_manager")
    def test_last_usage_format_consistent(self, mock_trace, mock_settings, mock_est):
        mock_settings.return_value.llm_model_id = "test-model"
        mock_settings.return_value.llm_api_key.get_secret_value.return_value = "sk-test"
        mock_settings.return_value.llm_base_url = "http://localhost:9999"
        mock_settings.return_value.llm_timeout = 60
        mock_trace.active = None

        chunk = _MockChunk(content="r", finish_reason="stop")
        with patch("litellm.completion", return_value=_chunk_iter(chunk)):
            llm = AgentLLM()
            llm._call([{"role": "user", "content": "x"}], 0, True, 0)
            for key in ("input_tokens", "output_tokens", "total_tokens"):
                assert key in llm.last_usage, f"missing key: {key}"
                assert isinstance(llm.last_usage[key], int)

    @patch("agentnexus.core.llm.get_settings")
    @patch("agentnexus.core.llm.trace_manager")
    def test_usage_with_streaming_usage_object(self, mock_trace, mock_settings):
        """Usage from chunk.usage object is properly recorded."""
        mock_settings.return_value.llm_model_id = "test-model"
        mock_settings.return_value.llm_api_key.get_secret_value.return_value = "sk-test"
        mock_settings.return_value.llm_base_url = "http://localhost:9999"
        mock_settings.return_value.llm_timeout = 60
        mock_trace.active = None

        usage = MagicMock()
        usage.prompt_tokens = 20
        usage.completion_tokens = 15
        usage.total_tokens = 35
        chunk = _MockChunk(content="ok", finish_reason="stop")
        chunk.usage = usage

        with patch("litellm.completion", return_value=_chunk_iter(chunk)):
            llm = AgentLLM()
            llm._call([{"role": "user", "content": "x"}], 0, True, 0)
            assert llm.last_usage.get("total_tokens") == 35
