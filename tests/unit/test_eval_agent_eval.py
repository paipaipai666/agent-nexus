"""Tests for agentnexus.evaluation.agent_eval — helpers, dataclasses, and evaluator methods."""

import pytest

from agentnexus.evaluation.agent_eval import (
    AgentEvaluator,
    AgentReport,
    TraceRecord,
    _cost,
    _percentile,
    _resolve_model,
)


class TestResolveModel:
    def test_alias_deepseek_chat(self):
        assert _resolve_model("deepseek-chat") == "deepseek-v3"

    def test_alias_deepseek_reasoner(self):
        assert _resolve_model("deepseek-reasoner") == "deepseek-r1"

    def test_direct_match(self):
        assert _resolve_model("gpt-4o") == "gpt-4o"

    def test_partial_match(self):
        assert _resolve_model("custom-deepseek-v4-flash") == "deepseek-v4-flash"

    def test_unknown_model(self):
        assert _resolve_model("unknown-model") == "unknown-model"


class TestCost:
    def test_known_model(self):
        assert _cost(1_000_000, 0, "deepseek-v4-flash") == 0.6

    def test_unknown_model(self):
        assert _cost(1000, 500, "unknown") == 0.0

    def test_zero_tokens(self):
        assert _cost(0, 0, "deepseek-v4-flash") == 0.0

    def test_mixed_cost(self):
        cost = _cost(1_000_000, 500_000, "gpt-4o")
        assert cost == pytest.approx(17.5 + 35.0)


class TestPercentile:
    def test_empty(self):
        assert _percentile([], 50) == 0.0

    def test_single(self):
        assert _percentile([42.0], 50) == 42.0

    def test_p50(self):
        vals = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
        assert _percentile(vals, 50) == 6.0

    def test_p95(self):
        vals = list(range(1, 101))
        assert _percentile(vals, 95) == 96.0

    def test_p99(self):
        vals = list(range(1, 101))
        assert _percentile(vals, 99) == 100.0

    def test_p0(self):
        vals = [10, 20, 30]
        assert _percentile(vals, 0) == 10.0

    def test_p100(self):
        vals = [10, 20, 30]
        assert _percentile(vals, 100) == 30.0


class TestTraceRecord:
    def test_defaults(self):
        rec = TraceRecord(trace_id="test")
        assert rec.task_preview == ""
        assert rec.steps == 0
        assert rec.tool_calls_total == 0
        assert rec.tool_calls_unique == []
        assert not rec.had_answer
        assert not rec.had_error
        assert not rec.had_truncation
        assert rec.total_input_tokens == 0
        assert rec.total_output_tokens == 0
        assert rec.total_latency_ms == 0.0
        assert rec.cost_cny == 0.0

    def test_cost_cny(self):
        rec = TraceRecord(trace_id="t", total_input_tokens=1_000_000, total_output_tokens=0)
        assert rec.cost_cny == 0.6

    def test_cost_cny_with_output(self):
        rec = TraceRecord(trace_id="t", total_input_tokens=1_000_000, total_output_tokens=500_000)
        assert rec.cost_cny == pytest.approx(0.6 + 0.6)

    def test_tool_calls_unique(self):
        rec = TraceRecord(trace_id="t", tool_calls_unique=["search", "read", "search"])
        assert len(rec.tool_calls_unique) == 3  # stored as-is, dedup in _evaluate_trace


class TestAgentReport:
    def test_defaults(self):
        report = AgentReport()
        assert report.total_traces == 0
        assert report.traces == []
        assert report.failed_traces == []
        assert not report.passed

    def test_passed_all_good(self):
        report = AgentReport()
        report.answer_rate = 0.9
        report.tool_success_rate = 0.9
        report.truncation_rate = 0.0
        report.error_rate = 0.0
        assert report.passed

    def test_passed_at_threshold(self):
        report = AgentReport()
        report.answer_rate = 0.85
        report.tool_success_rate = 0.80
        report.truncation_rate = 0.099
        report.error_rate = 0.099
        assert report.passed

    def test_passed_answer_rate_fails(self):
        report = AgentReport()
        report.answer_rate = 0.5
        report.tool_success_rate = 0.9
        report.truncation_rate = 0.0
        report.error_rate = 0.0
        assert not report.passed

    def test_passed_tool_success_rate_fails(self):
        report = AgentReport()
        report.answer_rate = 0.9
        report.tool_success_rate = 0.5
        report.truncation_rate = 0.0
        report.error_rate = 0.0
        assert not report.passed

    def test_passed_truncation_rate_fails(self):
        report = AgentReport()
        report.answer_rate = 0.9
        report.tool_success_rate = 0.9
        report.truncation_rate = 0.15
        report.error_rate = 0.0
        assert not report.passed

    def test_passed_error_rate_fails(self):
        report = AgentReport()
        report.answer_rate = 0.9
        report.tool_success_rate = 0.9
        report.truncation_rate = 0.0
        report.error_rate = 0.15
        assert not report.passed

    def test_summary_format(self):
        report = AgentReport(total_traces=5)
        report.answer_rate = 0.9
        report.avg_steps = 3.0
        report.tool_success_rate = 0.9
        report.truncation_rate = 0.05
        report.error_rate = 0.05
        summary = report.summary()
        assert "5 条 trace" in summary
        assert "90.0%" in summary

    def test_summary_passed_indicator(self):
        report = AgentReport(total_traces=1)
        report.answer_rate = 0.9
        report.tool_success_rate = 0.9
        report.truncation_rate = 0.0
        report.error_rate = 0.0
        summary = report.summary()
        assert "[PASS]" in summary

    def test_summary_failed_indicator(self):
        report = AgentReport(total_traces=1)
        report.answer_rate = 0.5
        report.tool_success_rate = 0.9
        report.truncation_rate = 0.0
        report.error_rate = 0.0
        summary = report.summary()
        assert "[FAIL]" in summary


class TestAgentEvaluatorEvaluateTrace:
    def test_no_llm_spans(self):
        spans = [{"name": "task", "trace_id": "test", "input": {"task": "hello"}}]
        rec = AgentEvaluator._evaluate_trace("test", spans)
        assert rec.task_preview == "hello"
        assert rec.steps == 0
        assert not rec.had_error
        assert not rec.had_truncation

    def test_task_preview_fallback_from_llm(self):
        spans = [
            {"name": "llm", "trace_id": "test", "input": {"input_preview": "fallback task"}},
        ]
        rec = AgentEvaluator._evaluate_trace("test", spans)
        assert rec.task_preview == "fallback task"

    def test_task_preview_empty_when_no_input(self):
        spans = [{"name": "task", "trace_id": "test", "input": {}}]
        rec = AgentEvaluator._evaluate_trace("test", spans)
        assert rec.task_preview == ""

    def test_with_llm_spans(self):
        spans = [
            {"name": "task", "trace_id": "test", "input": {"task": "hello"}},
            {"name": "llm", "trace_id": "test", "metadata": {
                "status": "ok", "input_tokens": 10, "output_tokens": 20,
                "tool_calls": ["search"],
            }, "latency_ms": 100},
            {"name": "llm", "trace_id": "test", "metadata": {
                "status": "ok", "input_tokens": 5, "output_tokens": 10,
            }, "latency_ms": 50},
        ]
        rec = AgentEvaluator._evaluate_trace("test", spans)
        assert rec.steps == 2
        assert rec.total_input_tokens == 15
        assert rec.total_output_tokens == 30
        assert rec.total_latency_ms == 150
        assert rec.tool_calls_total == 1
        assert rec.tool_calls_unique == ["search"]
        assert rec.tool_counts == {"search": 1}
        assert rec.had_answer

    def test_with_error(self):
        spans = [
            {"name": "task", "trace_id": "test", "input": {"task": "hello"}},
            {"name": "llm", "trace_id": "test", "metadata": {"status": "error", "truncated": True}, "latency_ms": 50},
        ]
        rec = AgentEvaluator._evaluate_trace("test", spans)
        assert rec.had_error
        assert rec.had_truncation

    def test_answer_false_when_last_span_has_tool_calls(self):
        spans = [
            {"name": "task", "trace_id": "test", "input": {"task": "hello"}},
            {"name": "llm", "trace_id": "test", "metadata": {"tool_calls": ["search"]}},
            {"name": "llm", "trace_id": "test", "metadata": {"tool_calls": ["read"]}},
        ]
        rec = AgentEvaluator._evaluate_trace("test", spans)
        assert not rec.had_answer

    def test_multiple_tool_calls(self):
        spans = [
            {"name": "task", "trace_id": "test", "input": {"task": "do it"}},
            {"name": "llm", "trace_id": "test", "metadata": {"tool_calls": ["search", "read"]}},
        ]
        rec = AgentEvaluator._evaluate_trace("test", spans)
        assert rec.tool_calls_total == 2
        assert "search" in rec.tool_calls_unique
        assert "read" in rec.tool_calls_unique
        assert rec.tool_counts == {"search": 1, "read": 1}


class TestAgentEvaluatorAggregate:
    def test_empty_records(self):
        report = AgentEvaluator._aggregate([])
        assert report.total_traces == 0
        assert report.answer_rate == 0.0
        assert report.tool_success_rate == 0.0
        assert report.traces == []

    def test_single_record(self):
        rec = TraceRecord(
            trace_id="test", steps=2,
            total_input_tokens=100, total_output_tokens=50,
            total_latency_ms=200.0, had_answer=True,
            tool_counts={"search": 1},
        )
        report = AgentEvaluator._aggregate([rec])
        assert report.total_traces == 1
        assert report.answer_rate == 1.0
        assert report.avg_steps == 2.0
        assert report.total_input_tokens == 100
        assert report.total_output_tokens == 50
        assert report.latency_p50_ms == 200.0
        assert report.avg_trace_latency_ms == 200.0
        assert report.tool_success_rate == 1.0

    def test_multiple_records(self):
        recs = [
            TraceRecord(
                trace_id="a", steps=1,
                total_input_tokens=10, total_output_tokens=5,
                total_latency_ms=100.0, had_answer=True,
                tool_counts={"read": 1},
            ),
            TraceRecord(
                trace_id="b", steps=3,
                total_input_tokens=20, total_output_tokens=15,
                total_latency_ms=300.0, had_answer=False,
                tool_counts={"search": 2}, had_error=True,
            ),
        ]
        report = AgentEvaluator._aggregate(recs)
        assert report.total_traces == 2
        assert report.answer_rate == 0.5
        assert report.avg_steps == 2.0
        assert report.total_input_tokens == 30
        assert report.total_output_tokens == 20
        assert report.total_cost_cny == pytest.approx(report.avg_cost_cny * 2)
        assert len(report.failed_traces) == 1
        assert report.failed_traces[0].trace_id == "b"
        assert report.tool_breakdown == {
            "read": {"calls": 1, "errors": 0, "success_rate": 1.0},
            "search": {"calls": 2, "errors": 0, "success_rate": 1.0},
        }

    def test_latency_percentiles(self):
        recs = [
            TraceRecord(trace_id=str(i), total_latency_ms=float(i * 100), had_answer=True)
            for i in range(1, 101)
        ]
        report = AgentEvaluator._aggregate(recs)
        assert report.latency_p50_ms == 5100.0
        assert report.latency_p95_ms == 9600.0
        assert report.latency_p99_ms == 10000.0
        assert report.avg_trace_latency_ms == pytest.approx(5050.0)


class TestLoadTraceFromFile:
    def test_matching_trace_returns_spans(self, tmp_path):
        f = tmp_path / "traces.jsonl"
        f.write_text(
            '{"trace_id": "abc", "name": "task"}\n'
            '{"trace_id": "abc", "name": "llm"}\n'
            '{"trace_id": "xyz", "name": "task"}\n'
        )
        spans = AgentEvaluator._load_trace_from_file(str(f), "abc")
        assert spans is not None
        assert len(spans) == 2
        assert all(s["trace_id"] == "abc" for s in spans)

    def test_no_matching_trace_returns_none(self, tmp_path):
        f = tmp_path / "traces.jsonl"
        f.write_text('{"trace_id": "other", "name": "task"}\n')
        spans = AgentEvaluator._load_trace_from_file(str(f), "notfound")
        assert spans is None

    def test_skips_invalid_json(self, tmp_path):
        f = tmp_path / "traces.jsonl"
        f.write_text(
            '{"trace_id": "abc", "name": "task"}\n'
            'invalid json\n'
            '{"trace_id": "abc", "name": "llm"}\n'
        )
        spans = AgentEvaluator._load_trace_from_file(str(f), "abc")
        assert spans is not None
        assert len(spans) == 2

    def test_file_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            AgentEvaluator._load_trace_from_file(
                str(tmp_path / "nonexistent.jsonl"), "abc"
            )


class TestAgentEvaluatorEvaluateTracePublic:
    def test_trace_not_found_returns_none(self, tmp_path):
        f = tmp_path / "traces" / "test.jsonl"
        f.parent.mkdir()
        f.write_text('{"trace_id": "other", "name": "task"}')
        result = AgentEvaluator().evaluate_trace("notfound", str(tmp_path / "traces"))
        assert result is None

    def test_found_returns_tracerecord(self, tmp_path):
        f = tmp_path / "traces" / "test.jsonl"
        f.parent.mkdir()
        f.write_text('{"trace_id": "t1", "name": "llm"}\n')
        result = AgentEvaluator().evaluate_trace("t1", str(tmp_path / "traces"))
        assert result is not None
        assert result.trace_id == "t1"

    def test_searches_files_in_reverse_order(self, tmp_path):
        early = tmp_path / "traces" / "a.jsonl"
        late = tmp_path / "traces" / "b.jsonl"
        early.parent.mkdir()
        early.write_text('{"trace_id": "t1", "name": "task", "input": {"task": "from_a"}}\n')
        late.write_text('{"trace_id": "t1", "name": "task", "input": {"task": "from_b"}}\n')
        result = AgentEvaluator().evaluate_trace("t1", str(tmp_path / "traces"))
        assert result is not None
        assert result.task_preview == "from_b"


class TestAgentEvaluatorEvaluateAll:
    def test_empty_directory_returns_empty_report(self, tmp_path):
        traces_dir = tmp_path / "traces"
        traces_dir.mkdir()
        report = AgentEvaluator().evaluate_all(str(traces_dir))
        assert report.total_traces == 0
        assert not report.passed

    def test_multiple_traces(self, tmp_path):
        traces_dir = tmp_path / "traces"
        traces_dir.mkdir()
        data = (
            '{"trace_id": "a", "name": "task", "input": {"task": "hello"}}\n'
            + '{"trace_id": "a", "name": "llm", "metadata": {'
            '"status": "ok", "input_tokens": 10, "output_tokens": 5}, '
            + '"latency_ms": 100}\n'
            + '{"trace_id": "b", "name": "task", "input": {"task": "world"}}\n'
            + '{"trace_id": "b", "name": "llm", "metadata": {'
            '"status": "error", "input_tokens": 5, "output_tokens": 10}, '
            + '"latency_ms": 50}\n'
        )
        (traces_dir / "day1.jsonl").write_text(data)
        report = AgentEvaluator().evaluate_all(str(traces_dir))
        assert report.total_traces == 2
        assert report.answer_rate == 1.0  # last llm span for each trace has no tool_calls
        assert report.error_rate == 0.5
        assert report.total_input_tokens == 15
        assert report.total_output_tokens == 15
