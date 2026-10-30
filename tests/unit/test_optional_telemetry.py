"""Observation follows module ownership and disappears without collecting state."""

import subprocess
import sys
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from textwrap import dedent
from types import SimpleNamespace

from agentnexus.core.modules import ModuleFactories, ModuleHost


class RecordingObserver:
    """Independent observer used through application module selection."""

    def __init__(self):
        self.events = []
        self.audit_log = []
        self.active = None
        self.inherited = None

    @contextmanager
    def span(self, name, input_data=None):
        self.events.append((name, input_data))
        yield SimpleNamespace(output={}, metadata={})

    def start_trace(self, task, metadata=None):
        self.events.append(("start", task))
        return None

    def end_trace(self):
        self.events.append(("end", None))

    def set_inherited_trace(self, trace_id):
        self.inherited = trace_id

    def get_inherited_trace(self):
        return self.inherited


def test_disabled_telemetry_does_not_import_observability_or_retain_spans(tmp_path):
    code = dedent(
        """
        import asyncio
        import importlib.abc
        import sys
        from pathlib import Path
        from types import SimpleNamespace
        from agentnexus.core.modules import ModuleFactories, ModuleHost

        class MissingObservability(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname == "agentnexus.observability" or fullname.startswith("agentnexus.observability."):
                    raise ModuleNotFoundError(name=fullname)
                return None

        sys.meta_path.insert(0, MissingObservability())
        from agentnexus.core.telemetry import append_audit, create_drift_detector, get_audit_log, trace_manager, trace_span
        from agentnexus.core.llm import AgentLLM
        from agentnexus.agents.re_act_agent import ReActAgent
        from agentnexus.tools.registry import ToolMeta, ToolRegistry

        host = ModuleHost(SimpleNamespace(traces_dir=sys.argv[1]), factories=ModuleFactories.disabled())
        def attributes():
            raise AssertionError("Disabled instrumentation evaluated attributes")

        @trace_span("sync", attributes)
        def sync_call():
            return "sync result"

        @trace_span("async", attributes)
        async def async_call():
            return "async result"

        with host.activate():
            assert sync_call() == "sync result"
            assert asyncio.run(async_call()) == "async result"
            assert trace_manager.start_trace("task") is None
            assert trace_manager.active is None
            with trace_manager.span("discard") as span:
                span.output = {"secret": "discarded"}
                span.metadata = {"secret": "discarded"}
                assert span.output == {}
                assert span.metadata == {}
            trace_manager.end_trace()
            append_audit({"tool": "discarded"})
            assert get_audit_log() == []
            assert create_drift_detector("task", 3).check(step_index=1) == []
            registry = ToolRegistry()
            registry.register(ToolMeta(name="read", description="Read", param_schema={}), lambda: "value")
            assert registry.invoke("read", {}) == "value"
            assert registry.get_audit_log() == []
            registry.close(wait=True)
        assert not Path(sys.argv[1]).exists()
        assert not any(name == "agentnexus.observability" or name.startswith("agentnexus.observability.") for name in sys.modules)
        print("Disabled instrumentation preserved behavior without observations")
        """
    )
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path / "traces")],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "Disabled instrumentation preserved behavior without observations"


def test_decorated_call_uses_current_runtime_replacement():
    from agentnexus.core.telemetry import append_audit, get_audit_log, trace_span

    first, second = RecordingObserver(), RecordingObserver()
    a = ModuleHost(SimpleNamespace(), factories=replace(ModuleFactories.disabled(), observability=lambda _: first))
    b = ModuleHost(SimpleNamespace(), factories=replace(ModuleFactories.disabled(), observability=lambda _: second))

    @trace_span("work", lambda value: {"value": value})
    def work(value):
        append_audit(value)
        return value * 2

    with a.activate():
        assert work(2) == 4
        with b.activate():
            assert work(3) == 6
            assert get_audit_log() == [3]
        assert work(4) == 8
        assert get_audit_log() == [2, 4]
    assert first.events == [("work", {"value": 2}), ("work", {"value": 4})]
    assert second.events == [("work", {"value": 3})]


def test_builtin_observers_do_not_share_trace_or_audit_state(tmp_path):
    from agentnexus.core.telemetry import append_audit, get_audit_log, trace_manager
    from agentnexus.observability.backend import ObservabilityBackend

    def host(folder):
        settings = SimpleNamespace(traces_dir=str(tmp_path / folder), trace_retention_days=30)
        return ModuleHost(settings, factories=replace(ModuleFactories.disabled(), observability=lambda ctx: ObservabilityBackend(ctx.settings)))

    a, b = host("a"), host("b")
    with a.activate():
        first = trace_manager.start_trace("a")
        append_audit({"owner": "a"})
        with b.activate():
            assert trace_manager.active is None
            assert get_audit_log() == []
            second = trace_manager.start_trace("b")
            assert second.trace_id != first.trace_id
            with trace_manager.span("b-tool"):
                pass
            trace_manager.end_trace()
        assert trace_manager.active is first
        assert get_audit_log() == [{"owner": "a"}]
        with trace_manager.span("a-tool"):
            pass
        trace_manager.end_trace()
    assert {span.name for span in first.spans} == {"task", "a-tool"}
    assert {span.name for span in second.spans} == {"task", "b-tool"}
    assert list((tmp_path / "a").glob("*.jsonl"))
    assert list((tmp_path / "b").glob("*.jsonl"))
    a.close()
    b.close()


def test_parallel_tools_use_the_selected_observation_sink():
    from agentnexus.core.telemetry import trace_span
    from agentnexus.tools.dispatcher import ToolDispatcher
    from agentnexus.tools.registry import ToolMeta, ToolRegistry

    observer = RecordingObserver()
    host = ModuleHost(SimpleNamespace(), factories=replace(ModuleFactories.disabled(), observability=lambda _: observer))

    @trace_span("worker")
    def read(value):
        return value * 2

    registry = ToolRegistry()
    registry.register(ToolMeta(name="read", description="Read", param_schema={}, concurrency_safe=True), read)
    with host.activate():
        results = ToolDispatcher(registry).execute(
            [{"name": "read", "arguments": {"value": 2}}, {"name": "read", "arguments": {"value": 3}}],
            registry.invoke,
        )
    registry.close(wait=True)
    assert [result.result for result in results] == [4, 6]
    assert [name for name, _ in observer.events].count("worker") == 2
    assert [entry.tool_name for entry in observer.audit_log] == ["read", "read"]


def test_observation_http_queries_use_replacement_backend():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from agentnexus.server.routes.stats import router

    class QueryableObserver(RecordingObserver):
        def stats(self, days=7):
            return {"total_tasks": days}

        def evaluate_stats(self, stats):
            self.events.append(("evaluated", stats))

        def list_logs(self, days=7):
            return {"traces": [{"trace_id": "replacement"}], "total": 1}

        def get_trace(self, trace_id):
            return {"trace_id": trace_id, "spans": [{"name": "replacement"}]}

    observer = QueryableObserver()
    host = ModuleHost(SimpleNamespace(), factories=replace(ModuleFactories.disabled(), observability=lambda _: observer))
    app = FastAPI()
    app.include_router(router)
    with host.activate(), TestClient(app) as client:
        assert client.get("/stats?days=3").json() == {"total_tasks": 3}
        assert client.get("/logs").json() == {"traces": [{"trace_id": "replacement"}], "total": 1}
        assert client.get("/logs/selected").json() == {"trace_id": "selected", "spans": [{"name": "replacement"}]}
    assert observer.events == [("evaluated", {"total_tasks": 3})]


def test_observation_operation_failures_preserve_work_and_its_own_exception():
    import pytest

    from agentnexus.core.telemetry import (
        append_audit,
        create_drift_detector,
        emit_drift_alerts,
        evaluate_run_alerts,
        trace_manager,
        trace_span,
    )

    class BrokenObserver(RecordingObserver):
        def span(self, name, input_data=None):
            raise OSError("span storage unavailable")

        def end_trace(self):
            raise TypeError("unserializable observation")

        def emit_drift_alerts(self, signals, *, trace_id=""):
            raise OSError("alert storage unavailable")

        def create_drift_detector(self, original_goal, max_steps):
            raise RuntimeError("drift provider failed")

        def stats(self, days=7):
            raise OSError("metrics storage unavailable")

    observer = BrokenObserver()
    host = ModuleHost(SimpleNamespace(), factories=replace(ModuleFactories.disabled(), observability=lambda _: observer))

    @trace_span("working")
    def work(value):
        trace_manager.end_trace()
        emit_drift_alerts(["signal"])
        evaluate_run_alerts()
        return value * 2

    @trace_span("failing")
    def fail():
        raise ValueError("task failure")

    with host.activate():
        assert work(3) == 6
        assert create_drift_detector("task", 3).check(step_index=1) == []
        with pytest.raises(ValueError, match="task failure"):
            fail()
        with trace_manager.span("direct"):
            append_audit("completed")
        assert observer.audit_log == ["completed"]


def test_observation_exit_failures_do_not_replace_task_exceptions():
    import pytest

    from agentnexus.core.telemetry import trace_span

    class BrokenExitObserver(RecordingObserver):
        @contextmanager
        def span(self, name, input_data=None):
            try:
                yield SimpleNamespace(output={}, metadata={})
            finally:
                raise OSError("observation cleanup failed")

    observer = BrokenExitObserver()
    host = ModuleHost(SimpleNamespace(), factories=replace(ModuleFactories.disabled(), observability=lambda _: observer))

    @trace_span("working")
    def work():
        return "completed"

    @trace_span("failing")
    def fail():
        raise ValueError("original task error")

    with host.activate():
        assert work() == "completed"
        with pytest.raises(ValueError, match="original task error"):
            fail()


def test_observation_factory_errors_remain_explicit():
    import pytest

    from agentnexus.core.modules import ModuleLoadError
    from agentnexus.core.telemetry import trace_manager

    def broken_factory(context):
        raise ValueError("invalid observer configuration")

    host = ModuleHost(SimpleNamespace(), factories=replace(ModuleFactories.disabled(), observability=broken_factory))
    with host.activate(), pytest.raises(ModuleLoadError, match="invalid observer configuration"):
        trace_manager.start_trace("work")
