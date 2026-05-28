"""Tests for agentnexus/cli/logs.py — _read_trace_spans, logs_list, logs_view."""
import json
import os
import time
from pathlib import Path

from typer.testing import CliRunner

from agentnexus.cli import app
from agentnexus.cli.logs import _read_trace_spans

runner = CliRunner()


class TestReadTraceSpans:
    """_read_trace_spans: reads *.jsonl from traces_dir, filters by start_time >= cutoff."""

    def _write_spans(self, traces_dir: Path, spans: list[dict], filename: str = "test.jsonl"):
        os.makedirs(traces_dir, exist_ok=True)
        fpath = traces_dir / filename
        with open(fpath, "a", encoding="utf-8") as f:
            for span in spans:
                f.write(json.dumps(span, ensure_ascii=False) + "\n")

    def test_no_dir_returns_empty(self, temp_agentnexus_home):
        spans = _read_trace_spans(days=7)
        assert spans == []

    def test_reads_spans_from_jsonl(self, temp_agentnexus_home):
        traces_dir = temp_agentnexus_home / "traces"
        now = time.time()
        span = {
            "trace_id": "t1",
            "span_id": "s1",
            "name": "task",
            "start_time": now,
            "end_time": now + 1.0,
            "latency_ms": 200,
            "metadata": {"model": "test", "input_tokens": 100, "output_tokens": 50, "status": "ok"},
        }
        self._write_spans(traces_dir, [span])
        spans = _read_trace_spans(days=7)
        assert len(spans) == 1
        assert spans[0]["trace_id"] == "t1"

    def test_filters_by_days(self, temp_agentnexus_home):
        traces_dir = temp_agentnexus_home / "traces"
        now = time.time()
        old = {
            "trace_id": "old",
            "span_id": "s_old",
            "name": "old_task",
            "start_time": now - 14 * 86400,
            "end_time": now - 14 * 86400 + 1,
            "latency_ms": 100,
            "metadata": {"status": "ok"},
        }
        new = {
            "trace_id": "new",
            "span_id": "s_new",
            "name": "new_task",
            "start_time": now,
            "end_time": now + 1,
            "latency_ms": 100,
            "metadata": {"status": "ok"},
        }
        self._write_spans(traces_dir, [old, new])
        spans = _read_trace_spans(days=7)
        assert len(spans) == 1
        assert spans[0]["trace_id"] == "new"

    def test_multiple_trace_files(self, temp_agentnexus_home):
        traces_dir = temp_agentnexus_home / "traces"
        now = time.time()
        span_a = {
            "trace_id": "t_a",
            "span_id": "s_a",
            "name": "a",
            "start_time": now,
            "end_time": now + 0.5,
            "latency_ms": 500,
            "metadata": {"status": "ok"},
        }
        span_b = {
            "trace_id": "t_b",
            "span_id": "s_b",
            "name": "b",
            "start_time": now,
            "end_time": now + 0.3,
            "latency_ms": 300,
            "metadata": {"status": "ok"},
        }
        self._write_spans(traces_dir, [span_a], filename="file_a.jsonl")
        self._write_spans(traces_dir, [span_b], filename="file_b.jsonl")
        spans = _read_trace_spans(days=7)
        ids = {s["trace_id"] for s in spans}
        assert "t_a" in ids
        assert "t_b" in ids

    def test_skips_bad_json_lines(self, temp_agentnexus_home):
        traces_dir = temp_agentnexus_home / "traces"
        os.makedirs(traces_dir, exist_ok=True)
        now = time.time()
        fpath = traces_dir / "bad.jsonl"
        with open(fpath, "w", encoding="utf-8") as f:
            f.write(json.dumps({"start_time": now, "trace_id": "ok1"}) + "\n")
            f.write("not valid json\n")
            f.write(json.dumps({"start_time": now, "trace_id": "ok2"}) + "\n")
        spans = _read_trace_spans(days=7)
        assert len(spans) == 2

    def test_empty_file_returns_empty(self, temp_agentnexus_home):
        traces_dir = temp_agentnexus_home / "traces"
        os.makedirs(traces_dir, exist_ok=True)
        (traces_dir / "empty.jsonl").write_text("", encoding="utf-8")
        spans = _read_trace_spans(days=7)
        assert spans == []


class TestLogsList:
    """logs_list: aggregates spans by trace_id, renders Rich Table."""

    def _write_span(self, traces_dir: Path, span: dict):
        os.makedirs(traces_dir, exist_ok=True)
        fpath = traces_dir / "test.jsonl"
        with open(fpath, "a", encoding="utf-8") as f:
            f.write(json.dumps(span, ensure_ascii=False) + "\n")

    def test_no_data_prints_empty_message(self, temp_agentnexus_home):
        result = runner.invoke(app, ["logs", "list"])
        assert "暂无 trace" in result.stdout
        assert result.exit_code == 0

    def test_with_single_trace(self, temp_agentnexus_home):
        traces_dir = temp_agentnexus_home / "traces"
        now = time.time()
        span = {
            "trace_id": "tr-001",
            "span_id": "sp-001",
            "name": "task",
            "start_time": now,
            "end_time": now + 0.5,
            "latency_ms": 500,
            "metadata": {"model": "gpt-4", "input_tokens": 10, "output_tokens": 20, "status": "ok"},
        }
        self._write_span(traces_dir, span)
        result = runner.invoke(app, ["logs", "list"])
        assert "tr-001" in result.stdout
        assert result.exit_code == 0

    def test_with_multiple_traces(self, temp_agentnexus_home):
        traces_dir = temp_agentnexus_home / "traces"
        now = time.time()
        for i in range(3):
            span = {
                "trace_id": f"tr-{i:03d}",
                "span_id": f"sp-{i:03d}",
                "name": f"task_{i}",
                "start_time": now + i,
                "end_time": now + i + 0.5,
                "latency_ms": 500,
                "metadata": {"input_tokens": 10, "output_tokens": 5, "status": "ok"},
            }
            self._write_span(traces_dir, span)
        result = runner.invoke(app, ["logs", "list"])
        assert "tr-000" in result.stdout
        assert "tr-001" in result.stdout
        assert "tr-002" in result.stdout
        assert result.exit_code == 0

    def test_error_status_shown(self, temp_agentnexus_home):
        traces_dir = temp_agentnexus_home / "traces"
        now = time.time()
        span = {
            "trace_id": "err-tr",
            "span_id": "err-sp",
            "name": "failing",
            "start_time": now,
            "end_time": now + 1,
            "latency_ms": 1000,
            "metadata": {"input_tokens": 5, "output_tokens": 0, "status": "error"},
        }
        self._write_span(traces_dir, span)
        result = runner.invoke(app, ["logs", "list"])
        assert "err-tr" in result.stdout
        assert result.exit_code == 0


class TestLogsView:
    """logs_view: finds spans for trace_id, renders Rich Tree."""

    def _write_span(self, traces_dir: Path, span: dict):
        os.makedirs(traces_dir, exist_ok=True)
        fpath = traces_dir / "test.jsonl"
        with open(fpath, "a", encoding="utf-8") as f:
            f.write(json.dumps(span, ensure_ascii=False) + "\n")

    def test_trace_not_found(self, temp_agentnexus_home):
        traces_dir = temp_agentnexus_home / "traces"
        os.makedirs(traces_dir, exist_ok=True)
        result = runner.invoke(app, ["logs", "view", "--trace-id", "nonexistent"])
        assert "未找到 Trace" in result.stdout
        assert result.exit_code == 0

    def test_single_span_trace(self, temp_agentnexus_home):
        traces_dir = temp_agentnexus_home / "traces"
        now = time.time()
        span = {
            "trace_id": "tr-001",
            "span_id": "root",
            "parent_span_id": "",
            "name": "root_span",
            "start_time": now,
            "end_time": now + 0.5,
            "latency_ms": 500,
            "metadata": {"model": "gpt-4", "input_tokens": 10, "output_tokens": 20, "status": "ok"},
        }
        self._write_span(traces_dir, span)
        result = runner.invoke(app, ["logs", "view", "--trace-id", "tr-001"])
        assert "tr-001" in result.stdout
        assert "root_span" in result.stdout
        assert "Span 总数: 1" in result.stdout
        assert result.exit_code == 0

    def test_parent_child_spans(self, temp_agentnexus_home):
        traces_dir = temp_agentnexus_home / "traces"
        now = time.time()
        root = {
            "trace_id": "tr-001",
            "span_id": "root",
            "parent_span_id": "",
            "name": "root_span",
            "start_time": now,
            "end_time": now + 0.5,
            "latency_ms": 500,
            "metadata": {"model": "gpt-4", "input_tokens": 10, "output_tokens": 20, "status": "ok"},
        }
        child = {
            "trace_id": "tr-001",
            "span_id": "child",
            "parent_span_id": "root",
            "name": "child_span",
            "start_time": now + 0.1,
            "end_time": now + 0.3,
            "latency_ms": 200,
            "metadata": {"model": "gpt-4", "input_tokens": 5, "output_tokens": 10, "status": "ok"},
        }
        self._write_span(traces_dir, root)
        self._write_span(traces_dir, child)
        result = runner.invoke(app, ["logs", "view", "--trace-id", "tr-001"])
        assert "root_span" in result.stdout
        assert "child_span" in result.stdout
        assert "Span 总数: 2" in result.stdout
        assert result.exit_code == 0

    def test_error_span_summary(self, temp_agentnexus_home):
        traces_dir = temp_agentnexus_home / "traces"
        now = time.time()
        span = {
            "trace_id": "err-tr",
            "span_id": "err-sp",
            "parent_span_id": "",
            "name": "failing_span",
            "start_time": now,
            "end_time": now + 1,
            "latency_ms": 1000,
            "metadata": {"model": "gpt-4", "input_tokens": 10, "output_tokens": 5, "status": "error"},
        }
        self._write_span(traces_dir, span)
        result = runner.invoke(app, ["logs", "view", "--trace-id", "err-tr"])
        assert "err-tr" in result.stdout
        assert "错误: 1 个 span" in result.stdout
        assert result.exit_code == 0
