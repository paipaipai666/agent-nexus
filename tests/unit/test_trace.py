
from types import SimpleNamespace
from unittest.mock import MagicMock

from agentnexus.observability.tracer import (
    TraceContext,
    TraceManager,
    TraceSpan,
    trace_manager,
)
from agentnexus.tools.subagent import make_subagent_run


class TestTraceContext:
    def test_start_span_creates_valid_span(self):
        ctx = TraceContext()
        span = ctx.start_span("test_op", {"key": "value"})
        assert isinstance(span, TraceSpan)
        assert span.name == "test_op"
        assert span.input == {"key": "value"}
        assert span.span_id
        assert len(span.span_id) == 8  # uuid[:8]

    def test_end_span_records_output(self):
        ctx = TraceContext()
        span = ctx.start_span("op")
        ctx.end_span(span, output_data={"result": "done"}, metadata={"tokens": 100})
        assert span in ctx.spans
        assert span.end_time > 0
        assert span.output["result"] == "done"
        assert span.metadata["tokens"] == 100
        assert span.end_time >= span.start_time

    def test_span_hierarchy_parent_id(self):
        ctx = TraceContext()
        root = ctx.start_span("root")
        ctx._span_stack.append(root)  # simulate parent on stack
        child = ctx.start_span("child")
        assert child.parent_span_id == root.span_id

    def test_span_stack_lifo(self):
        ctx = TraceContext()
        parent = ctx.start_span("parent")
        child = ctx.start_span("child")
        assert len(ctx._span_stack) == 2
        ctx.end_span(child)
        assert len(ctx._span_stack) == 1
        assert ctx._span_stack[0].span_id == parent.span_id

    def test_span_latency_ms(self):
        span = TraceSpan(
            span_id="12345678",
            name="op",
            start_time=1000.0,
            end_time=1001.5,
        )
        assert span.latency_ms == 1500.0

    def test_span_status_from_metadata(self):
        span = TraceSpan(span_id="12345678")
        assert span.status == "ok"
        span.metadata = {"status": "error"}
        assert span.status == "error"


class TestTraceManager:
    def test_singleton(self):
        tm1 = TraceManager()
        tm2 = TraceManager()
        assert tm1 is tm2

    def test_start_and_end_trace(self, tmp_path):
        tm = TraceManager()
        tm.configure(str(tmp_path))
        ctx = tm.start_trace("test task")
        assert tm.active is ctx
        assert tm.active.trace_id is not None

        span = ctx.start_span("child_op")
        ctx.end_span(span, metadata={"status": "ok"})

        tm.end_trace()
        assert tm.active is None

        # verify JSONL file written
        jsonl_files = list(tmp_path.glob("*.jsonl"))
        assert len(jsonl_files) == 1

        content = jsonl_files[0].read_text(encoding="utf-8")
        assert "test task" in content
        assert "child_op" in content

    def test_end_trace_without_active_does_not_error(self):
        tm = TraceManager()
        # reset local
        if hasattr(tm._local, "trace"):
            del tm._local.trace
        tm.end_trace()  # should not raise

    def test_span_context_manager(self, tmp_path):
        tm = TraceManager()
        tm.configure(str(tmp_path))
        tm.start_trace("ctx manager test")

        with tm.span("operation", {"in": "data"}) as span:
            assert span.name == "operation"
            assert span.input == {"in": "data"}

        tm.end_trace()
        jsonl_files = list(tmp_path.glob("*.jsonl"))
        assert len(jsonl_files) >= 1
        content = jsonl_files[0].read_text(encoding="utf-8")
        assert "operation" in content

    def test_span_context_manager_outside_trace(self):
        tm = TraceManager()
        # reset active trace
        if hasattr(tm._local, "trace"):
            del tm._local.trace
        assert tm.active is None
        with tm.span("orphan_op") as span:
            assert span.name == "orphan_op"
        # should not raise

    def test_spans_recorded_in_order(self, tmp_path):
        tm = TraceManager()
        tm.configure(str(tmp_path))
        tm.start_trace("order test")

        s1 = trace_manager.active.start_span("first")
        trace_manager.active.end_span(s1)
        s2 = trace_manager.active.start_span("second")
        trace_manager.active.end_span(s2)

        tm.end_trace()
        jsonl_files = list(tmp_path.glob("*.jsonl"))
        content = jsonl_files[0].read_text(encoding="utf-8")
        first_idx = content.index("first")
        second_idx = content.index("second")
        assert first_idx < second_idx

    def test_subagent_trace_contains_parent_and_attempt_spans(self, tmp_path, monkeypatch):
        tm = TraceManager()
        tm.configure(str(tmp_path))
        tm.start_trace("subagent trace test")

        monkeypatch.setattr("agentnexus.tools.subagent._clone_llm", lambda _parent: MagicMock())

        def fake_run(self, question, memory_manager=None):
            ctx = trace_manager.active
            llm_span = ctx.start_span("llm", {"messages_count": 1})
            ctx.end_span(llm_span, metadata={"status": "ok"})
            return SimpleNamespace(answer="child answer", steps=[])

        monkeypatch.setattr("agentnexus.tools.subagent.ReActAgent.run", fake_run)

        tool = make_subagent_run(parent_llm=MagicMock(), non_interactive=True)
        payload = tool(task="请总结 README", role="explorer", max_steps=2)
        assert "child answer" in payload

        tm.end_trace()
        jsonl_files = list(tmp_path.glob("*.jsonl"))
        content = jsonl_files[0].read_text(encoding="utf-8")
        assert '"name": "subagent"' in content
        assert '"name": "subagent_attempt"' in content
        assert '"name": "llm"' in content

    def test_subagent_trace_records_recovery_metadata(self, tmp_path, monkeypatch):
        tm = TraceManager()
        tm.configure(str(tmp_path))
        tm.start_trace("subagent recovery trace")

        monkeypatch.setattr("agentnexus.tools.subagent._clone_llm", lambda _parent: MagicMock())
        monkeypatch.setattr(
            "agentnexus.tools.subagent.ReActAgent.run",
            lambda self, question, memory_manager=None: SimpleNamespace(answer="fallback answer", steps=[]),
        )

        tool = make_subagent_run(parent_llm=MagicMock(), non_interactive=True)
        payload = tool(task="请总结 README", role="reader", allowed_tools=["python_execute"], max_steps=2)
        assert "fallback answer" in payload

        tm.end_trace()
        jsonl_files = list(tmp_path.glob("*.jsonl"))
        content = jsonl_files[0].read_text(encoding="utf-8")
        assert 'requested_tools_filtered' in content

    def test_tool_and_final_answer_spans_capture_subagent_adoption(self, tmp_path, monkeypatch):
        from agentnexus.agents.react_types import ExecutionContext, ReActEvent, ReActEventType
        from agentnexus.tools.tool_executor import ToolExecutor
        from agentnexus.agents.re_act_agent import ReActAgent

        tm = TraceManager()
        tm.configure(str(tmp_path))
        tm.start_trace("parent adoption trace")

        mock_llm = MagicMock()
        agent = ReActAgent(mock_llm, ToolExecutor(), conversation_mode=False)
        ctx = ExecutionContext(question="请总结 README")
        ctx.steps.append(SimpleNamespace(tool_outputs=[]))
        ctx.last_answer = "最终答案"
        ctx.last_subagent_payload = {"answer": "child answer", "status": "fallback", "role": "explorer", "recovery": {"attempted": True}}

        agent._on_emit_answer(ctx, ReActEvent(ReActEventType.NO_TOOLS))
        tm.end_trace()
        jsonl_files = list(tmp_path.glob("*.jsonl"))
        content = jsonl_files[0].read_text(encoding="utf-8")
        assert '"name": "final_answer"' in content
        assert '"used_subagent": true' in content
        assert 'child answer' in content
