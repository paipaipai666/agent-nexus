"""Tests for agentnexus.observability.stats."""
import json
import time

from agentnexus.observability.stats import (
    TokenStats,
    _cost,
    _short_model,
    compute_stats,
)


class TestCost:
    def test_known_model(self):
        # (1000*0.6 + 500*1.2) / 1_000_000 = (600 + 600) / 1_000_000 = 0.0012
        assert _cost(1000, 500, "deepseek-v4-flash") == 0.0012

    def test_alias_model(self):
        # deepseek-chat resolves to deepseek-v3
        # (1000*1.0 + 500*2.0) / 1_000_000 = (1000 + 1000) / 1_000_000 = 0.002
        assert _cost(1000, 500, "deepseek-chat") == 0.002

    def test_unknown_model(self):
        assert _cost(1000, 500, "unknown-model") == 0.0

    def test_zero_tokens(self):
        assert _cost(0, 0, "deepseek-v4-flash") == 0.0


class TestShortModel:
    def test_alias_resolved(self):
        assert _short_model("deepseek-chat") == "deepseek-v3"

    def test_no_alias(self):
        assert _short_model("deepseek-v4-flash") == "deepseek-v4-flash"


class TestTokenStats:
    def test_defaults(self):
        s = TokenStats()
        assert s.total_tasks == 0
        assert s.total_input_tokens == 0
        assert s.total_output_tokens == 0
        assert s.total_cost_cny == 0.0
        assert s.avg_latency_ms == 0.0
        assert s.p95_latency_ms == 0.0
        assert s.p99_latency_ms == 0.0
        assert s.max_latency_ms == 0.0
        assert s.avg_retries == 0.0
        assert s.cost_per_query == 0.0
        assert s.by_model == {}
        assert s.by_date == {}


class TestComputeStats:
    def test_no_traces_dir(self, temp_agentnexus_home):
        stats = compute_stats(str(temp_agentnexus_home / "nonexistent"))
        assert stats.total_tasks == 0
        assert stats.total_input_tokens == 0

    def test_empty_traces_dir(self, temp_agentnexus_home):
        traces_dir = temp_agentnexus_home / "traces"
        traces_dir.mkdir(parents=True, exist_ok=True)
        # Create an empty jsonl file
        (traces_dir / "2025-01-01.jsonl").write_text("", encoding="utf-8")
        stats = compute_stats(str(traces_dir))
        assert stats.total_tasks == 0

    def test_single_task(self, temp_agentnexus_home):
        traces_dir = temp_agentnexus_home / "traces"
        traces_dir.mkdir(parents=True, exist_ok=True)
        now = time.time()
        lines = [
            json.dumps({
                "trace_id": "trace-1", "name": "task", "start_time": now,
                "latency_ms": 100.0, "input": {}, "output": {},
                "metadata": {"input_tokens": 100, "output_tokens": 50,
                             "model": "deepseek-v4-flash"},
            }),
            json.dumps({
                "trace_id": "trace-1", "name": "llm", "start_time": now,
                "latency_ms": 100.0, "input": {}, "output": {},
                "metadata": {"input_tokens": 100, "output_tokens": 50,
                             "model": "deepseek-v4-flash"},
            }),
        ]
        (traces_dir / "2025-01-01.jsonl").write_text(
            "\n".join(lines), encoding="utf-8"
        )
        stats = compute_stats(str(traces_dir))
        assert stats.total_tasks == 1
        assert stats.total_input_tokens == 200
        assert stats.total_output_tokens == 100
        assert stats.avg_latency_ms == 100.0
        assert stats.max_latency_ms == 100.0

    def test_multiple_tasks(self, temp_agentnexus_home):
        traces_dir = temp_agentnexus_home / "traces"
        traces_dir.mkdir(parents=True, exist_ok=True)
        now = time.time()
        lines = [
            json.dumps({
                "trace_id": "trace-1", "name": "task", "start_time": now,
                "latency_ms": 100.0, "input": {}, "output": {},
                "metadata": {"input_tokens": 100, "output_tokens": 50,
                             "model": "deepseek-v4-flash"},
            }),
            json.dumps({
                "trace_id": "trace-2", "name": "task", "start_time": now,
                "latency_ms": 200.0, "input": {}, "output": {},
                "metadata": {"input_tokens": 200, "output_tokens": 100,
                             "model": "deepseek-v4-flash"},
            }),
        ]
        (traces_dir / "2025-01-01.jsonl").write_text(
            "\n".join(lines), encoding="utf-8"
        )
        stats = compute_stats(str(traces_dir))
        assert stats.total_tasks == 2
        assert stats.total_input_tokens == 300
        assert stats.total_output_tokens == 150
        assert stats.avg_latency_ms == 150.0
        assert stats.max_latency_ms == 200.0

    def test_retries(self, temp_agentnexus_home):
        traces_dir = temp_agentnexus_home / "traces"
        traces_dir.mkdir(parents=True, exist_ok=True)
        now = time.time()
        lines = [
            json.dumps({
                "trace_id": "trace-1", "name": "task", "start_time": now,
                "latency_ms": 10.0, "input": {}, "output": {},
                "metadata": {},
            }),
            json.dumps({
                "trace_id": "trace-1", "name": "plan_node", "start_time": now,
                "latency_ms": 10.0, "input": {}, "output": {}, "metadata": {},
            }),
            json.dumps({
                "trace_id": "trace-1", "name": "plan_node", "start_time": now,
                "latency_ms": 10.0, "input": {}, "output": {}, "metadata": {},
            }),
            json.dumps({
                "trace_id": "trace-2", "name": "task", "start_time": now,
                "latency_ms": 10.0, "input": {}, "output": {}, "metadata": {},
            }),
            json.dumps({
                "trace_id": "trace-2", "name": "plan_node", "start_time": now,
                "latency_ms": 10.0, "input": {}, "output": {}, "metadata": {},
            }),
        ]
        (traces_dir / "2025-01-01.jsonl").write_text(
            "\n".join(lines), encoding="utf-8"
        )
        stats = compute_stats(str(traces_dir))
        # trace-1 has 2 plan_nodes -> 1 retry, trace-2 has 1 plan_node -> 0 retries
        # avg_retries = (1 + 0) / 2 = 0.5
        assert stats.avg_retries == 0.5

    def test_filters_by_days(self, temp_agentnexus_home):
        traces_dir = temp_agentnexus_home / "traces"
        traces_dir.mkdir(parents=True, exist_ok=True)
        now = time.time()
        lines = [
            json.dumps({
                "trace_id": "trace-old", "name": "task", "start_time": 0,
                "latency_ms": 10.0, "input": {}, "output": {},
                "metadata": {"input_tokens": 999, "output_tokens": 999,
                             "model": "deepseek-v4-flash"},
            }),
            json.dumps({
                "trace_id": "trace-new", "name": "task", "start_time": now,
                "latency_ms": 10.0, "input": {}, "output": {},
                "metadata": {"input_tokens": 100, "output_tokens": 50,
                             "model": "deepseek-v4-flash"},
            }),
        ]
        (traces_dir / "2025-01-01.jsonl").write_text(
            "\n".join(lines), encoding="utf-8"
        )
        stats = compute_stats(str(traces_dir), days=7)
        assert stats.total_tasks == 1
        assert stats.total_input_tokens == 100
        assert stats.total_output_tokens == 50

    def test_skips_bad_json(self, temp_agentnexus_home):
        traces_dir = temp_agentnexus_home / "traces"
        traces_dir.mkdir(parents=True, exist_ok=True)
        now = time.time()
        content = (
            json.dumps({
                "trace_id": "trace-1", "name": "task", "start_time": now,
                "latency_ms": 10.0, "input": {}, "output": {},
                "metadata": {"input_tokens": 100, "output_tokens": 50,
                             "model": "deepseek-v4-flash"},
            })
            + "\n"
            + "this is not valid json\n"
            + "\n"
            + json.dumps({
                "trace_id": "trace-2", "name": "task", "start_time": now,
                "latency_ms": 10.0, "input": {}, "output": {},
                "metadata": {"input_tokens": 200, "output_tokens": 100,
                             "model": "deepseek-v4-flash"},
            })
            + "\n"
        )
        (traces_dir / "2025-01-01.jsonl").write_text(content, encoding="utf-8")
        stats = compute_stats(str(traces_dir))
        assert stats.total_tasks == 2
        assert stats.total_input_tokens == 300
        assert stats.total_output_tokens == 150

    def test_by_model_breakdown(self, temp_agentnexus_home):
        traces_dir = temp_agentnexus_home / "traces"
        traces_dir.mkdir(parents=True, exist_ok=True)
        now = time.time()
        lines = [
            json.dumps({
                "trace_id": "trace-1", "name": "task", "start_time": now,
                "latency_ms": 10.0, "input": {}, "output": {},
                "metadata": {"input_tokens": 300, "output_tokens": 100,
                             "model": "deepseek-v3"},
            }),
            json.dumps({
                "trace_id": "trace-2", "name": "task", "start_time": now,
                "latency_ms": 10.0, "input": {}, "output": {},
                "metadata": {"input_tokens": 100, "output_tokens": 50,
                             "model": "deepseek-v4-flash"},
            }),
        ]
        (traces_dir / "2025-01-01.jsonl").write_text(
            "\n".join(lines), encoding="utf-8"
        )
        stats = compute_stats(str(traces_dir))
        assert "deepseek-v3" in stats.by_model
        assert "deepseek-v4-flash" in stats.by_model
        # deepseek-v3: 300 input, 100 output, cost = (300*1.0 + 100*2.0)/1e6 = 0.0005
        assert stats.by_model["deepseek-v3"]["input_tokens"] == 300
        assert stats.by_model["deepseek-v3"]["output_tokens"] == 100
        assert stats.by_model["deepseek-v3"]["cost_cny"] == 0.0005
        # deepseek-v4-flash: 100 input, 50 output, cost = (100*0.6 + 50*1.2)/1e6 = 0.00012
        assert stats.by_model["deepseek-v4-flash"]["input_tokens"] == 100
        assert stats.by_model["deepseek-v4-flash"]["output_tokens"] == 50
        assert stats.by_model["deepseek-v4-flash"]["cost_cny"] == 0.0001

    def test_by_date_breakdown(self, temp_agentnexus_home):
        traces_dir = temp_agentnexus_home / "traces"
        traces_dir.mkdir(parents=True, exist_ok=True)
        now = time.time()
        day1_lines = [
            json.dumps({
                "trace_id": "trace-1", "name": "task", "start_time": now,
                "latency_ms": 10.0, "input": {}, "output": {},
                "metadata": {"input_tokens": 100, "output_tokens": 50,
                             "model": "deepseek-v4-flash"},
            }),
        ]
        day2_lines = [
            json.dumps({
                "trace_id": "trace-2", "name": "task", "start_time": now,
                "latency_ms": 10.0, "input": {}, "output": {},
                "metadata": {"input_tokens": 200, "output_tokens": 100,
                             "model": "deepseek-v3"},
            }),
        ]
        (traces_dir / "2025-01-01.jsonl").write_text(
            "\n".join(day1_lines), encoding="utf-8"
        )
        (traces_dir / "2025-01-02.jsonl").write_text(
            "\n".join(day2_lines), encoding="utf-8"
        )
        stats = compute_stats(str(traces_dir))
        assert "2025-01-02" in stats.by_date
        assert "2025-01-01" in stats.by_date
        assert stats.by_date["2025-01-01"]["deepseek-v4-flash"]["input"] == 100
        assert stats.by_date["2025-01-01"]["deepseek-v4-flash"]["output"] == 50
        assert stats.by_date["2025-01-02"]["deepseek-v3"]["input"] == 200
        assert stats.by_date["2025-01-02"]["deepseek-v3"]["output"] == 100
