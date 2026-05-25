"""Tests for private helper functions in tracer.py: _truncate, _truncate_dict, _flush_span, _cleanup_old_traces."""

import json
import os
import time
from pathlib import Path

from agentnexus.observability.tracer import (
    _truncate,
    _truncate_dict,
    TraceContext,
    TraceManager,
    TraceSpan,
)


class TestTruncate:
    def test_short_text_unchanged(self):
        text = "Hello, world!"
        assert _truncate(text) == text

    def test_long_text_truncated(self):
        text = "x" * 6000
        result = _truncate(text)
        assert result.endswith("...[截断 1000 字符]")
        assert len(result) < len(text)

    def test_custom_max_len(self):
        text = "x" * 100
        result = _truncate(text, max_len=50)
        assert result.endswith("...[截断 50 字符]")
        assert len(result) < 100

    def test_empty_text(self):
        assert _truncate("") == ""

    def test_exact_limit(self):
        text = "x" * 5000
        assert _truncate(text) == text


class TestTruncateDict:
    def test_truncates_long_values(self):
        d = {"key": "x" * 6000}
        result = _truncate_dict(d)
        assert "截断" in result["key"]
        assert len(result["key"]) < 6000

    def test_short_values_unchanged(self):
        d = {"key": "short"}
        assert _truncate_dict(d) == {"key": "short"}

    def test_empty_dict(self):
        assert _truncate_dict({}) == {}

    def test_numeric_values(self):
        d = {"a": 123, "b": 45.67}
        result = _truncate_dict(d)
        assert result["a"] == "123"
        assert result["b"] == "45.67"

    def test_mixed_values(self):
        d = {"short": "hi", "long": "x" * 6000, "num": 42}
        result = _truncate_dict(d)
        assert result["short"] == "hi"
        assert "截断" in result["long"]
        assert result["num"] == "42"


class TestFlushSpan:
    def test_flush_span_writes_single_span(self, tmp_path):
        tm = TraceManager()
        tm.configure(str(tmp_path))
        ctx = TraceContext()
        span = ctx.start_span("test_op")
        ctx.end_span(span, output_data={"result": "ok"})

        tm._flush_span(ctx, span)

        jsonl_files = list(Path(tmp_path).glob("*.jsonl"))
        assert len(jsonl_files) == 1
        record = json.loads(jsonl_files[0].read_text(encoding="utf-8"))
        assert record["name"] == "test_op"
        assert record["output"]["result"] == "ok"

    def test_flush_span_idempotent(self, tmp_path):
        tm = TraceManager()
        tm.configure(str(tmp_path))
        ctx = TraceContext()
        span = ctx.start_span("test_op")
        ctx.end_span(span)

        tm._flush_span(ctx, span)
        tm._flush_span(ctx, span)

        jsonl_files = list(Path(tmp_path).glob("*.jsonl"))
        assert len(jsonl_files) == 1
        content = jsonl_files[0].read_text(encoding="utf-8")
        assert content.count("\n") == 1

    def test_flush_span_no_traces_dir(self):
        tm = TraceManager()
        TraceManager.configure("")
        ctx = TraceContext()
        span = ctx.start_span("test_op")
        ctx.end_span(span)

        tm._flush_span(ctx, span)

    def test_flush_span_creates_dir(self, tmp_path):
        nested = tmp_path / "nested" / "traces"
        assert not nested.exists()
        tm = TraceManager()
        tm.configure(str(nested))
        ctx = TraceContext()
        span = ctx.start_span("test_op")
        ctx.end_span(span)

        tm._flush_span(ctx, span)

        assert nested.exists()
        assert len(list(nested.glob("*.jsonl"))) == 1


class TestCleanupOldTraces:
    def test_removes_old_files(self, tmp_path, mocker):
        tm = TraceManager()
        tm.configure(str(tmp_path))
        mocker.patch("agentnexus.core.config.get_settings",
                     side_effect=Exception("force default retention"))

        old_file = tmp_path / "old.jsonl"
        old_file.write_text("{}")
        old_mtime = time.time() - 31 * 86400
        os.utime(str(old_file), (old_mtime, old_mtime))

        recent_file = tmp_path / "recent.jsonl"
        recent_file.write_text("{}")
        recent_mtime = time.time() - 1 * 86400
        os.utime(str(recent_file), (recent_mtime, recent_mtime))

        tm._cleanup_old_traces()

        assert not old_file.exists()
        assert recent_file.exists()

    def test_keeps_recent_files(self, tmp_path, mocker):
        tm = TraceManager()
        tm.configure(str(tmp_path))
        mocker.patch("agentnexus.core.config.get_settings",
                     side_effect=Exception("force default retention"))

        for i in range(3):
            f = tmp_path / f"file_{i}.jsonl"
            f.write_text("{}")
            f_mtime = time.time() - i * 86400
            os.utime(str(f), (f_mtime, f_mtime))

        tm._cleanup_old_traces()

        files = list(tmp_path.glob("*.jsonl"))
        assert len(files) == 3

    def test_uses_retention_days_from_settings(self, tmp_path, mocker):
        tm = TraceManager()
        tm.configure(str(tmp_path))

        mock_settings = mocker.MagicMock()
        mock_settings.trace_retention_days = 7
        mocker.patch("agentnexus.core.config.get_settings",
                     return_value=mock_settings)

        old_file = tmp_path / "old.jsonl"
        old_file.write_text("{}")
        old_mtime = time.time() - 10 * 86400
        os.utime(str(old_file), (old_mtime, old_mtime))

        recent_file = tmp_path / "recent.jsonl"
        recent_file.write_text("{}")
        recent_mtime = time.time() - 1 * 86400
        os.utime(str(recent_file), (recent_mtime, recent_mtime))

        tm._cleanup_old_traces()

        assert not old_file.exists()
        assert recent_file.exists()
