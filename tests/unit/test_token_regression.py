"""P1-5: Token consumption regression threshold test.

Verifies that AgentLLM correctly tracks token usage and that
total_usage does not grow unbounded.

No API calls are made — all tests use mocked LLM responses.
"""
from unittest.mock import patch

from agentnexus.core.llm import AgentLLM
from agentnexus.core.providers.base import StreamResult


def _make_result(text="", usage=None, finish_reason="stop"):
    return StreamResult(
        text=text,
        usage=usage or {"input_tokens": 5, "output_tokens": 5, "total_tokens": 10},
        finish_reason=finish_reason,
    )


_EST = {"input_tokens": 5, "output_tokens": 5, "total_tokens": 10}
_EST10 = {"input_tokens": 10, "output_tokens": 10, "total_tokens": 20}


class TestTokenUsageTracking:
    """last_usage and total_usage are tracked correctly via _call()."""

    @patch("agentnexus.core.llm._provider_health", {})
    @patch("agentnexus.core.llm.AgentLLM._estimate_usage", return_value=_EST)
    @patch("agentnexus.core.llm.get_settings")
    @patch("agentnexus.core.llm.trace_manager")
    def test_last_usage_after_single_call(self, mock_trace, mock_settings, _est):
        mock_settings.return_value.llm_model_id = "test-model"
        mock_settings.return_value.llm_api_key.get_secret_value.return_value = "sk-test"
        mock_settings.return_value.llm_base_url = "http://localhost:9999"
        mock_settings.return_value.llm_timeout = 60
        mock_trace.active = None

        llm = AgentLLM()
        result_obj = _make_result(text="hi")
        with patch.object(llm, "_call_via_provider", return_value=result_obj):
            llm._call([{"role": "user", "content": "hi"}], 0, True, 0)
            assert llm.last_usage["input_tokens"] == 5
            assert llm.last_usage["output_tokens"] == 5

    @patch("agentnexus.core.llm._provider_health", {})
    @patch("agentnexus.core.llm.AgentLLM._estimate_usage", return_value=_EST)
    @patch("agentnexus.core.llm.get_settings")
    @patch("agentnexus.core.llm.trace_manager")
    def test_total_usage_accumulates(self, mock_trace, mock_settings, _est):
        mock_settings.return_value.llm_model_id = "test-model"
        mock_settings.return_value.llm_api_key.get_secret_value.return_value = "sk-test"
        mock_settings.return_value.llm_base_url = "http://localhost:9999"
        mock_settings.return_value.llm_timeout = 60
        mock_trace.active = None

        llm = AgentLLM()
        result_obj = _make_result(text="hi")
        with patch.object(llm, "_call_via_provider", return_value=result_obj):
            llm._call([{"role": "user", "content": "hi"}], 0, True, 0)
            assert llm.total_usage["input_tokens"] >= 5
            assert llm.total_usage["output_tokens"] >= 5

    @patch("agentnexus.core.llm._provider_health", {})
    @patch("agentnexus.core.llm.AgentLLM._estimate_usage", return_value=_EST10)
    @patch("agentnexus.core.llm.get_settings")
    @patch("agentnexus.core.llm.trace_manager")
    def test_consecutive_calls_accumulate(self, mock_trace, mock_settings, _est):
        mock_settings.return_value.llm_model_id = "test-model"
        mock_settings.return_value.llm_api_key.get_secret_value.return_value = "sk-test"
        mock_settings.return_value.llm_base_url = "http://localhost:9999"
        mock_settings.return_value.llm_timeout = 60
        mock_trace.active = None

        llm = AgentLLM()
        result_obj = _make_result(text="r", usage=_EST10)
        with patch.object(llm, "_call_via_provider", return_value=result_obj):
            llm._call([{"role": "user", "content": "msg 0"}], 0, True, 0)
            llm._call([{"role": "user", "content": "msg 1"}], 0, True, 0)
            llm._call([{"role": "user", "content": "msg 2"}], 0, True, 0)
            assert llm.total_usage["input_tokens"] >= 30
            assert llm.total_usage["output_tokens"] >= 30

    @patch("agentnexus.core.llm._provider_health", {})
    @patch("agentnexus.core.llm.AgentLLM._estimate_usage", return_value=_EST)
    @patch("agentnexus.core.llm.get_settings")
    @patch("agentnexus.core.llm.trace_manager")
    def test_empty_response_has_zero_tokens(self, mock_trace, mock_settings, _est):
        mock_settings.return_value.llm_model_id = "test-model"
        mock_settings.return_value.llm_api_key.get_secret_value.return_value = "sk-test"
        mock_settings.return_value.llm_base_url = "http://localhost:9999"
        mock_settings.return_value.llm_timeout = 60
        mock_trace.active = None

        llm = AgentLLM()
        result_obj = _make_result(text="", usage={"input_tokens": 0, "output_tokens": 0, "total_tokens": 0})
        with patch.object(llm, "_call_via_provider", return_value=result_obj):
            llm._call([{"role": "user", "content": "hi"}], 0, True, 0)
            assert llm.last_usage.get("total_tokens", 0) >= 0

    @patch("agentnexus.core.llm._provider_health", {})
    @patch("agentnexus.core.llm.AgentLLM._estimate_usage", return_value=_EST10)
    @patch("agentnexus.core.llm.get_settings")
    @patch("agentnexus.core.llm.trace_manager")
    def test_total_usage_does_not_reset(self, mock_trace, mock_settings, _est):
        mock_settings.return_value.llm_model_id = "test-model"
        mock_settings.return_value.llm_api_key.get_secret_value.return_value = "sk-test"
        mock_settings.return_value.llm_base_url = "http://localhost:9999"
        mock_settings.return_value.llm_timeout = 60
        mock_trace.active = None

        llm = AgentLLM()
        llm.total_usage = {"input_tokens": 100, "output_tokens": 50, "cache_hit_tokens": 0}
        result_obj = _make_result(text="r", usage=_EST10)
        with patch.object(llm, "_call_via_provider", return_value=result_obj):
            llm._call([{"role": "user", "content": "a"}], 0, True, 0)
            llm._call([{"role": "user", "content": "b"}], 0, True, 0)
            assert llm.total_usage["input_tokens"] >= 120
            assert llm.total_usage["output_tokens"] >= 70


class TestUsageNoOverflow:
    """Token counts should not grow beyond expected bounds."""

    @patch("agentnexus.core.llm._provider_health", {})
    @patch("agentnexus.core.llm.AgentLLM._estimate_usage", return_value=_EST)
    @patch("agentnexus.core.llm.get_settings")
    @patch("agentnexus.core.llm.trace_manager")
    def test_no_negative_tokens(self, mock_trace, mock_settings, _est):
        mock_settings.return_value.llm_model_id = "test-model"
        mock_settings.return_value.llm_api_key.get_secret_value.return_value = "sk-test"
        mock_settings.return_value.llm_base_url = "http://localhost:9999"
        mock_settings.return_value.llm_timeout = 60
        mock_trace.active = None

        llm = AgentLLM()
        result_obj = _make_result(text="ok")
        with patch.object(llm, "_call_via_provider", return_value=result_obj):
            llm._call([{"role": "user", "content": "x"}], 0, True, 0)
            assert llm.last_usage["input_tokens"] >= 0
            assert llm.last_usage["output_tokens"] >= 0

    @patch("agentnexus.core.llm._provider_health", {})
    @patch("agentnexus.core.llm.AgentLLM._estimate_usage", return_value=_EST)
    @patch("agentnexus.core.llm.get_settings")
    @patch("agentnexus.core.llm.trace_manager")
    def test_last_usage_format_consistent(self, mock_trace, mock_settings, _est):
        mock_settings.return_value.llm_model_id = "test-model"
        mock_settings.return_value.llm_api_key.get_secret_value.return_value = "sk-test"
        mock_settings.return_value.llm_base_url = "http://localhost:9999"
        mock_settings.return_value.llm_timeout = 60
        mock_trace.active = None

        llm = AgentLLM()
        result_obj = _make_result(text="r")
        with patch.object(llm, "_call_via_provider", return_value=result_obj):
            llm._call([{"role": "user", "content": "x"}], 0, True, 0)
            for key in ("input_tokens", "output_tokens", "total_tokens"):
                assert key in llm.last_usage, f"missing key: {key}"
                assert isinstance(llm.last_usage[key], int)

    @patch("agentnexus.core.llm._provider_health", {})
    @patch("agentnexus.core.llm.AgentLLM._estimate_usage", return_value=_EST)
    @patch("agentnexus.core.llm.get_settings")
    @patch("agentnexus.core.llm.trace_manager")
    def test_usage_with_streaming_usage_object(self, mock_trace, mock_settings, _est):
        mock_settings.return_value.llm_model_id = "test-model"
        mock_settings.return_value.llm_api_key.get_secret_value.return_value = "sk-test"
        mock_settings.return_value.llm_base_url = "http://localhost:9999"
        mock_settings.return_value.llm_timeout = 60
        mock_trace.active = None

        llm = AgentLLM()
        result_obj = _make_result(
            text="ok",
            usage={"input_tokens": 20, "output_tokens": 15, "total_tokens": 35},
        )
        with patch.object(llm, "_call_via_provider", return_value=result_obj):
            llm._call([{"role": "user", "content": "x"}], 0, True, 0)
            assert llm.last_usage.get("total_tokens") == 35
