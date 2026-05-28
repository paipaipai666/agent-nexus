import pytest

from agentnexus.observability.tracer import (
    TraceContext,
    TraceManager,
    TraceSpan,
    _truncate,
    _truncate_dict,
)


@pytest.fixture(autouse=True)
def _reset_trace_manager():
    TraceManager._instance = None
    yield
    TraceManager._instance = None


class TestTraceContextStartEndSpan:

    def test_start_span_creates_valid_span(self):
        ctx = TraceContext()
        span = ctx.start_span("op", {"key": "value"})
        assert isinstance(span, TraceSpan)
        assert span.name == "op"
        assert span.input == {"key": "value"}
        assert span.span_id

    def test_end_span_records_span(self):
        ctx = TraceContext()
        span = ctx.start_span("op")
        ctx.end_span(span, output_data={"result": "done"}, metadata={"status": "ok"})
        assert span in ctx.spans
        assert span.end_time >= span.start_time
        assert span.output["result"] == "done"
        assert span.metadata["status"] == "ok"


class TestTraceSpanLatency:

    def test_latency_ms_computed_correctly(self):
        span = TraceSpan(span_id="12345678", name="op", start_time=1000.0, end_time=1001.5)
        assert span.latency_ms == 1500.0



class TestTraceSpanStatus:

    def test_status_ok_by_default(self):
        span = TraceSpan(span_id="12345678")
        assert span.status == "ok"

    def test_status_error_when_set(self):
        span = TraceSpan(span_id="12345678", metadata={"status": "error"})
        assert span.status == "error"


class TestEndSpanIdempotent:

    def test_same_span_not_duplicated(self):
        ctx = TraceContext()
        span = ctx.start_span("op")
        ctx.end_span(span)
        ctx.end_span(span)
        assert len(ctx.spans) == 1


class TestTraceManagerSingleton:

    def test_singleton_returns_same_instance(self):
        tm1 = TraceManager()
        tm2 = TraceManager()
        assert tm1 is tm2

    def test_configure_sets_dir(self, tmp_path):
        tm = TraceManager()
        tm.configure(str(tmp_path))
        assert tm._traces_dir == str(tmp_path)

    def test_start_and_end_trace(self, tmp_path):
        tm = TraceManager()
        tm.configure(str(tmp_path))
        ctx = tm.start_trace("task")
        assert tm.active is ctx
        tm.end_trace()
        assert tm.active is None

    def test_end_trace_writes_jsonl(self, tmp_path):
        tm = TraceManager()
        tm.configure(str(tmp_path))
        tm.start_trace("task")
        tm.end_trace()
        jsonl_files = list(tmp_path.glob("*.jsonl"))
        assert len(jsonl_files) == 1
        content = jsonl_files[0].read_text(encoding="utf-8")
        assert "task" in content


class TestSpanContextManager:

    def test_span_context_manager_creates_and_ends_span(self, tmp_path):
        tm = TraceManager()
        tm.configure(str(tmp_path))
        tm.start_trace("task")

        with tm.span("my_op", {"in": "data"}) as span:
            assert span.name == "my_op"
            assert span.input == {"in": "data"}

        tm.end_trace()
        jsonl_files = list(tmp_path.glob("*.jsonl"))
        assert len(jsonl_files) >= 1
        content = jsonl_files[0].read_text(encoding="utf-8")
        assert "my_op" in content

    def test_span_context_manager_with_exception_sets_error(self, tmp_path):
        tm = TraceManager()
        tm.configure(str(tmp_path))
        tm.start_trace("task")

        with pytest.raises(ValueError, match="boom"):
            with tm.span("failing_op") as span:
                raise ValueError("boom")

        assert span.metadata["status"] == "error"
        assert "boom" in span.metadata["error"]

        tm.end_trace()

    def test_span_context_manager_without_active_trace(self):
        tm = TraceManager()
        if hasattr(tm._local, "trace"):
            del tm._local.trace
        with tm.span("orphan") as span:
            assert span.name == "orphan"
        assert span.end_time > 0


class TestTruncateDict:

    def test_truncate_dict_short_values_unchanged(self):
        d = {"key": "short"}
        result = _truncate_dict(d)
        assert result == {"key": "short"}

    def test_truncate_dict_long_values_truncated(self):
        long_val = "x" * 6000
        d = {"key": long_val}
        result = _truncate_dict(d)
        assert len(result["key"]) < len(long_val)
        assert "截断" in result["key"]

    def test_truncate_short_text_unchanged(self):
        assert _truncate("hello") == "hello"

    def test_truncate_long_text(self):
        text = "a" * 6000
        result = _truncate(text, max_len=5000)
        assert len(result) < len(text)
        assert "截断" in result
