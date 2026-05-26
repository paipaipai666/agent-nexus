"""Tests for agentnexus/cli/audit.py and agentnexus/cli/logs.py"""
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from typer.testing import CliRunner

from agentnexus.cli import app
from agentnexus.cli.logs import _read_trace_spans
from agentnexus.observability import audit_log as audit_mod
from agentnexus.observability.audit_log import append_audit, get_audit_log
from agentnexus.tools.registry import AuditEntry

runner = CliRunner()


class TestAudit:
    def setup_method(self):
        audit_mod._global_audit_log.clear()

    def test_audit_empty(self):
        result = runner.invoke(app, ["audit"])
        assert "暂无审计记录" in result.stdout
        assert result.exit_code == 0

    def test_audit_with_entries(self):
        append_audit(AuditEntry(
            tool_name="test_tool",
            caller="test_agent",
            params="{}",
            result_summary="success",
            duration_ms=150.0,
            hitl_triggered=False,
            error=None,
        ))
        result = runner.invoke(app, ["audit"])
        stdout = result.stdout
        assert "test_tool" in stdout
        assert "test_agent" in stdout
        assert "success" in stdout
        assert "150" in stdout
        assert result.exit_code == 0

    def test_audit_limit_filter(self):
        for i in range(5):
            append_audit(AuditEntry(
                tool_name=f"tool_{i}",
                caller="agent",
                params="{}",
                result_summary=f"result_{i}",
                duration_ms=float(i * 10),
                hitl_triggered=False,
                error=None,
            ))
        result = runner.invoke(app, ["audit", "--limit", "2"])
        stdout = result.stdout
        assert "tool_3" in stdout
        assert "tool_4" in stdout
        assert "tool_0" not in stdout
        assert "tool_1" not in stdout
        assert result.exit_code == 0

    def test_audit_tool_filter(self):
        append_audit(AuditEntry(
            tool_name="search", caller="agent1", params="{}",
            result_summary="found", duration_ms=10.0, hitl_triggered=False, error=None,
        ))
        append_audit(AuditEntry(
            tool_name="code", caller="agent2", params="{}",
            result_summary="executed", duration_ms=20.0, hitl_triggered=False, error=None,
        ))
        result = runner.invoke(app, ["audit", "--tool", "search"])
        stdout = result.stdout
        assert "search" in stdout
        assert "code" not in stdout
        assert result.exit_code == 0

    def test_get_audit_log_returns_copy(self):
        append_audit(AuditEntry(
            tool_name="test", caller="agent", params="{}",
            result_summary="ok", duration_ms=1.0, hitl_triggered=False, error=None,
        ))
        copy_log = get_audit_log()
        assert len(copy_log) == 1
        copy_log.clear()
        assert len(get_audit_log()) == 1

    def test_concurrent_append_and_snapshot(self):
        def write_entries(worker: int):
            for i in range(100):
                append_audit(AuditEntry(
                    tool_name=f"tool_{worker}",
                    caller="agent",
                    params="{}",
                    result_summary=f"ok_{i}",
                    duration_ms=1.0,
                    hitl_triggered=False,
                    error=None,
                ))
                _ = get_audit_log()

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(write_entries, range(8)))

        entries = get_audit_log()
        assert len(entries) == 800
        assert {e.tool_name for e in entries} == {f"tool_{i}" for i in range(8)}


class TestLogs:
    def _write_span(self, traces_dir, span_data):
        os.makedirs(traces_dir, exist_ok=True)
        fpath = Path(traces_dir) / "test.jsonl"
        with open(fpath, "a", encoding="utf-8") as f:
            f.write(json.dumps(span_data, ensure_ascii=False) + "\n")

    def test_read_trace_spans_no_dir(self, temp_agentnexus_home):
        spans = _read_trace_spans(days=7)
        assert spans == []

    def test_read_trace_spans_with_data(self, temp_agentnexus_home):
        traces_dir = temp_agentnexus_home / "traces"
        now = time.time()
        span = {
            "trace_id": "trace_001",
            "span_id": "span_001",
            "parent_span_id": "",
            "name": "test_span",
            "start_time": now,
            "end_time": now + 1.0,
            "latency_ms": 1000.0,
            "metadata": {"model": "gpt-4", "input_tokens": 10, "output_tokens": 20, "status": "ok"},
        }
        self._write_span(traces_dir, span)
        spans = _read_trace_spans(days=7)
        assert len(spans) == 1
        assert spans[0]["trace_id"] == "trace_001"

    def test_read_trace_spans_filters_by_days(self, temp_agentnexus_home):
        traces_dir = temp_agentnexus_home / "traces"
        now = time.time()
        old_span = {
            "trace_id": "old_trace",
            "span_id": "old_span",
            "parent_span_id": "",
            "name": "old",
            "start_time": now - 14 * 86400,
            "end_time": now - 14 * 86400 + 1.0,
            "latency_ms": 500.0,
            "metadata": {"status": "ok"},
        }
        new_span = {
            "trace_id": "new_trace",
            "span_id": "new_span",
            "parent_span_id": "",
            "name": "new",
            "start_time": now,
            "end_time": now + 1.0,
            "latency_ms": 100.0,
            "metadata": {"status": "ok"},
        }
        self._write_span(traces_dir, old_span)
        self._write_span(traces_dir, new_span)
        spans = _read_trace_spans(days=7)
        assert len(spans) == 1
        assert spans[0]["trace_id"] == "new_trace"

    def test_read_trace_spans_skips_bad_lines(self, temp_agentnexus_home):
        traces_dir = temp_agentnexus_home / "traces"
        os.makedirs(traces_dir, exist_ok=True)
        now = time.time()
        fpath = Path(traces_dir) / "test.jsonl"
        with open(fpath, "w", encoding="utf-8") as f:
            f.write(json.dumps({"start_time": now, "valid": "span1"}) + "\n")
            f.write("not valid json\n")
            f.write(json.dumps({"start_time": now, "valid": "span2"}) + "\n")
        spans = _read_trace_spans(days=7)
        assert len(spans) == 2

    def test_logs_list_no_data(self, temp_agentnexus_home):
        result = runner.invoke(app, ["logs", "list"])
        assert "暂无 trace" in result.stdout
        assert result.exit_code == 0

    def test_logs_list_with_data(self, temp_agentnexus_home):
        traces_dir = temp_agentnexus_home / "traces"
        now = time.time()
        span = {
            "trace_id": "trace_001",
            "span_id": "span_001",
            "parent_span_id": "",
            "name": "test_span",
            "start_time": now,
            "end_time": now + 0.5,
            "latency_ms": 500.0,
            "metadata": {"model": "gpt-4", "input_tokens": 10, "output_tokens": 20, "status": "ok"},
        }
        self._write_span(traces_dir, span)
        result = runner.invoke(app, ["logs", "list"])
        stdout = result.stdout
        assert "trace_001" in stdout
        assert result.exit_code == 0

    def test_logs_view_not_found(self, temp_agentnexus_home):
        traces_dir = temp_agentnexus_home / "traces"
        os.makedirs(traces_dir, exist_ok=True)
        result = runner.invoke(app, ["logs", "view", "--trace-id", "nonexistent"])
        assert "未找到 Trace" in result.stdout
        assert result.exit_code == 0

    def test_logs_view_with_data(self, temp_agentnexus_home):
        traces_dir = temp_agentnexus_home / "traces"
        now = time.time()
        root_span = {
            "trace_id": "trace_001",
            "span_id": "root",
            "parent_span_id": "",
            "name": "root_span",
            "start_time": now,
            "end_time": now + 0.5,
            "latency_ms": 500.0,
            "metadata": {"model": "gpt-4", "input_tokens": 10, "output_tokens": 20, "status": "ok"},
        }
        child_span = {
            "trace_id": "trace_001",
            "span_id": "child",
            "parent_span_id": "root",
            "name": "child_span",
            "start_time": now + 0.1,
            "end_time": now + 0.3,
            "latency_ms": 200.0,
            "metadata": {"model": "gpt-4", "input_tokens": 5, "output_tokens": 10, "status": "ok"},
        }
        self._write_span(traces_dir, root_span)
        self._write_span(traces_dir, child_span)
        result = runner.invoke(app, ["logs", "view", "--trace-id", "trace_001"])
        stdout = result.stdout
        assert "trace_001" in stdout
        assert "root_span" in stdout
        assert "child_span" in stdout
        assert "Span 总数: 2" in stdout
        assert result.exit_code == 0

    def test_logs_view_with_error_span(self, temp_agentnexus_home):
        traces_dir = temp_agentnexus_home / "traces"
        now = time.time()
        span = {
            "trace_id": "error_trace",
            "span_id": "span_001",
            "parent_span_id": "",
            "name": "failing_span",
            "start_time": now,
            "end_time": now + 1.0,
            "latency_ms": 1000.0,
            "metadata": {"model": "gpt-4", "input_tokens": 10, "output_tokens": 5, "status": "error"},
        }
        self._write_span(traces_dir, span)
        result = runner.invoke(app, ["logs", "view", "--trace-id", "error_trace"])
        stdout = result.stdout
        assert "error_trace" in stdout
        assert "错误: 1 个 span" in stdout
        assert result.exit_code == 0
