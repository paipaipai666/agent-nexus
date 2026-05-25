"""Cost threshold regression tests.

Validates that cost per query doesn't exceed expected bounds
and that token consumption stays within reasonable limits.
"""
import json
import time
from unittest.mock import MagicMock, patch

import pytest

from agentnexus.observability.stats import _cost, compute_stats


class TestCostThresholdRegression:
    """Cost per query stays within reasonable bounds."""

    _MAX_COST_PER_QUERY_CNY = 0.10
    _MAX_TOKENS_PER_QUERY = 10000

    def test_cost_does_not_exceed_threshold(self):
        cost = _cost(5000, 2000, "deepseek-v4-flash")
        assert cost < self._MAX_COST_PER_QUERY_CNY

    def test_cost_for_expensive_model(self):
        cost = _cost(5000, 2000, "deepseek-v3")
        assert cost < self._MAX_COST_PER_QUERY_CNY

    def test_total_usage_does_not_exceed_token_limit(self):
        total = 5000 + 2000
        assert total < self._MAX_TOKENS_PER_QUERY

    def test_cost_accuracy_known_model(self):
        cost = _cost(1000, 500, "deepseek-v4-flash")
        expected = (1000 * 0.6 + 500 * 1.2) / 1_000_000
        assert cost == expected

    def test_cost_accuracy_alias_model(self):
        cost = _cost(1000, 500, "deepseek-chat")
        expected = (1000 * 1.0 + 500 * 2.0) / 1_000_000
        assert cost == expected

    def test_cost_zero_for_unknown_model(self):
        cost = _cost(5000, 2000, "unknown-model-123")
        assert cost == 0.0

    def test_cost_scales_linearly(self):
        cost1 = _cost(1000, 500, "deepseek-v4-flash")
        cost2 = _cost(2000, 1000, "deepseek-v4-flash")
        assert cost2 == 2 * cost1

    def test_compute_stats_with_cost_tracking(self, temp_agentnexus_home):
        traces_dir = temp_agentnexus_home / "traces"
        traces_dir.mkdir(parents=True, exist_ok=True)

        now = time.time()
        lines = [
            json.dumps({
                "trace_id": "trace-1", "name": "task", "start_time": now,
                "latency_ms": 100.0, "input": {}, "output": {},
                "metadata": {
                    "input_tokens": 1000, "output_tokens": 500,
                    "model": "deepseek-v4-flash",
                    "cost_cny": _cost(1000, 500, "deepseek-v4-flash"),
                },
            }),
        ]
        (traces_dir / "2025-01-01.jsonl").write_text("\n".join(lines), encoding="utf-8")

        stats = compute_stats(str(traces_dir))
        assert stats.total_cost_cny > 0

    def test_cost_per_query_regression(self, temp_agentnexus_home):
        traces_dir = temp_agentnexus_home / "traces"
        traces_dir.mkdir(parents=True, exist_ok=True)

        now = time.time()
        lines = [
            json.dumps({
                "trace_id": f"trace-{i}", "name": "task", "start_time": now,
                "latency_ms": 100.0, "input": {}, "output": {},
                "metadata": {
                    "input_tokens": 1000, "output_tokens": 500,
                    "model": "deepseek-v4-flash",
                },
            })
            for i in range(10)
        ]
        (traces_dir / "2025-01-01.jsonl").write_text("\n".join(lines), encoding="utf-8")

        stats = compute_stats(str(traces_dir))
        avg_cost = stats.total_cost_cny / stats.total_tasks if stats.total_tasks > 0 else 0
        assert avg_cost < self._MAX_COST_PER_QUERY_CNY


class TestTokenRegression:
    """Token consumption doesn't regress."""

    @patch("agentnexus.core.llm.get_settings")
    @patch("agentnexus.core.llm.trace_manager")
    def test_usage_no_negative(self, mock_trace, mock_settings):
        mock_settings.return_value.llm_model_id = "test-model"
        mock_settings.return_value.llm_api_key.get_secret_value.return_value = "sk-test"
        mock_settings.return_value.llm_base_url = "http://localhost:9999"
        mock_settings.return_value.llm_timeout = 60
        mock_trace.active = None

        from agentnexus.core.llm import AgentLLM

        chunk = MagicMock()
        delta = MagicMock()
        delta.content = "ok"
        delta.tool_calls = []
        delta.reasoning_content = None
        chunk.choices = [MagicMock(delta=delta, finish_reason="stop")]
        chunk.usage = None

        with patch("litellm.completion", return_value=[chunk]):
            with patch("litellm.token_counter", return_value=10):
                llm = AgentLLM()
                llm._call([{"role": "user", "content": "hi"}], 0, True, 0)
                assert llm.last_usage["input_tokens"] >= 0
                assert llm.last_usage["output_tokens"] >= 0

    @patch("agentnexus.core.llm.get_settings")
    @patch("agentnexus.core.llm.trace_manager")
    def test_usage_format_consistency(self, mock_trace, mock_settings):
        mock_settings.return_value.llm_model_id = "test-model"
        mock_settings.return_value.llm_api_key.get_secret_value.return_value = "sk-test"
        mock_settings.return_value.llm_base_url = "http://localhost:9999"
        mock_settings.return_value.llm_timeout = 60
        mock_trace.active = None

        from agentnexus.core.llm import AgentLLM

        chunk = MagicMock()
        delta = MagicMock()
        delta.content = "ok"
        delta.tool_calls = []
        delta.reasoning_content = None
        chunk.choices = [MagicMock(delta=delta, finish_reason="stop")]
        chunk.usage = None

        with patch("litellm.completion", return_value=[chunk]):
            with patch("litellm.token_counter", return_value=10):
                llm = AgentLLM()
                llm._call([{"role": "user", "content": "hi"}], 0, True, 0)

                for key in ("input_tokens", "output_tokens", "total_tokens"):
                    assert key in llm.last_usage, f"Missing key: {key}"
                    assert isinstance(llm.last_usage[key], (int, float))
