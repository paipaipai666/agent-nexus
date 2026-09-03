"""Parallel subagent execution: lane routing, concurrency cap, cancel/trace propagation."""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

from agentnexus.observability.tracer import trace_manager
from agentnexus.tools.confirm_bridge import CancelBridge
from agentnexus.tools.dispatcher import ToolDispatcher
from agentnexus.tools.registry import ToolRegistry
from agentnexus.tools.subagent import make_subagent_run


def _make_registry(subagent_workers: int = 2) -> ToolRegistry:
    """Registry with a stub subagent tool on the dedicated lane."""
    registry = ToolRegistry()
    with patch("agentnexus.core.config.get_settings") as mock_settings:
        mock_settings.return_value.subagent_max_concurrent = subagent_workers
        registry.register_tool(
            "subagent_run", "stub", lambda **kw: "ok",
            concurrency_safe=True, lane="subagent",
        )
        registry.register_tool("web_search", "stub", lambda **kw: "ok", concurrency_safe=True)
        registry.register_tool("file_write", "stub", lambda **kw: "ok")
        # Pre-create the lane pool under the patched settings
        registry.get_lane_pool("subagent")
    return registry


class TestLaneRouting:
    def test_lane_tool_runs_on_lane_pool_thread(self):
        registry = _make_registry()
        thread_names: dict[str, str] = {}
        lock = threading.Lock()

        def record(name):
            def fn(**_kw):
                with lock:
                    thread_names[name] = threading.current_thread().name
                return "ok"
            return fn

        registry.register_tool("subagent_run", "stub", record("subagent_run"),
                               concurrency_safe=True, lane="subagent")
        registry.register_tool("web_search", "stub", record("web_search"), concurrency_safe=True)
        registry.register_tool("file_write", "stub", record("file_write"))

        calls = [
            {"name": "subagent_run", "arguments": {}},
            {"name": "web_search", "arguments": {}},
            {"name": "file_write", "arguments": {}},
        ]
        results = ToolDispatcher(registry).execute(calls, execute_fn=lambda n, a: registry.get_tool(n)(**a))

        assert [r.result for r in results] == ["ok", "ok", "ok"]  # order preserved
        assert thread_names["subagent_run"].startswith("lane-subagent")
        assert not thread_names["web_search"].startswith("lane-subagent")
        assert thread_names["file_write"] == threading.main_thread().name


class TestSubagentConcurrencyCap:
    def test_peak_concurrency_never_exceeds_cap(self):
        cap = 2
        registry = _make_registry(subagent_workers=cap)
        state = {"current": 0, "peak": 0}
        lock = threading.Lock()

        def slow(**_kw):
            with lock:
                state["current"] += 1
                state["peak"] = max(state["peak"], state["current"])
            time.sleep(0.15)
            with lock:
                state["current"] -= 1
            return "done"

        registry.register_tool("subagent_run", "stub", slow, concurrency_safe=True, lane="subagent")
        calls = [{"name": "subagent_run", "arguments": {}} for _ in range(5)]
        results = ToolDispatcher(registry).execute(calls, execute_fn=lambda n, a: registry.get_tool(n)(**a))

        assert [r.result for r in results] == ["done"] * 5
        assert state["peak"] == cap  # hits the cap but never exceeds it

    def test_lane_tasks_actually_run_in_parallel(self):
        registry = _make_registry(subagent_workers=2)

        def slow(**_kw):
            time.sleep(0.4)
            return "done"

        registry.register_tool("subagent_run", "stub", slow, concurrency_safe=True, lane="subagent")
        calls = [{"name": "subagent_run", "arguments": {}} for _ in range(2)]
        start = time.monotonic()
        ToolDispatcher(registry).execute(calls, execute_fn=lambda n, a: registry.get_tool(n)(**a))
        elapsed = time.monotonic() - start

        assert elapsed < 0.7  # serial execution would take ≥ 0.8s


class TestCancelBridgePropagation:
    def test_child_agent_receives_parent_cancel_checker(self, monkeypatch):
        monkeypatch.setattr("agentnexus.tools.subagent._clone_llm", lambda _parent: MagicMock())
        received = {}

        def fake_run(self, question, memory_manager=None):
            received["checker"] = self._cancel_checker
            return MagicMock(answer="ok", steps=[])

        monkeypatch.setattr("agentnexus.tools.subagent.ReActAgent.run", fake_run)
        monkeypatch.setattr("agentnexus.tools.subagent._register_child_tools", lambda *a, **k: None)

        bridge = CancelBridge()
        flag = {"cancelled": False}
        bridge.set_checker(lambda: flag["cancelled"])

        tool = make_subagent_run(parent_llm=MagicMock(), non_interactive=True, cancel_bridge=bridge)
        tool(task="t", role="explorer", max_steps=1)

        assert received["checker"] is not None
        assert received["checker"]() is False
        flag["cancelled"] = True
        assert received["checker"]() is True

    def test_no_bridge_leaves_child_without_checker(self, monkeypatch):
        monkeypatch.setattr("agentnexus.tools.subagent._clone_llm", lambda _parent: MagicMock())
        received = {}

        def fake_run(self, question, memory_manager=None):
            received["checker"] = self._cancel_checker
            return MagicMock(answer="ok", steps=[])

        monkeypatch.setattr("agentnexus.tools.subagent.ReActAgent.run", fake_run)
        monkeypatch.setattr("agentnexus.tools.subagent._register_child_tools", lambda *a, **k: None)

        tool = make_subagent_run(parent_llm=MagicMock(), non_interactive=True)
        tool(task="t", role="explorer", max_steps=1)

        assert received["checker"] is None


class TestInheritedTraceLinkage:
    def test_worker_thread_sees_parent_trace_and_it_is_cleared(self):
        registry = _make_registry()
        seen: list[str | None] = []
        lock = threading.Lock()

        def capture(**_kw):
            with lock:
                seen.append(trace_manager.get_inherited_trace())
            return "ok"

        registry.register_tool("web_search", "stub", capture, concurrency_safe=True)

        ctx = trace_manager.start_trace("parent task")
        try:
            calls = [{"name": "web_search", "arguments": {}}]
            ToolDispatcher(registry).execute(calls, execute_fn=lambda n, a: registry.get_tool(n)(**a))
            parent_id = ctx.trace_id
        finally:
            trace_manager.end_trace()

        # No active trace now — worker must see None, not a stale link
        ToolDispatcher(registry).execute(calls, execute_fn=lambda n, a: registry.get_tool(n)(**a))

        assert seen[0] == parent_id
        assert seen[1] is None

    def test_subagent_span_carries_parent_trace_id(self, monkeypatch):
        """Subagent spans must stamp the inherited trace id (worker threads
        have no active trace, so the link arrives via the dispatcher)."""
        monkeypatch.setattr("agentnexus.tools.subagent._clone_llm", lambda _parent: MagicMock())
        monkeypatch.setattr(
            "agentnexus.tools.subagent.ReActAgent.run",
            lambda self, question, memory_manager=None: MagicMock(answer="ok", steps=[]),
        )
        monkeypatch.setattr("agentnexus.tools.subagent._register_child_tools", lambda *a, **k: None)

        mock_trace = MagicMock()
        mock_trace.get_inherited_trace.return_value = "parent-trace-123"
        monkeypatch.setattr("agentnexus.tools.subagent.trace_manager", mock_trace)

        tool = make_subagent_run(parent_llm=MagicMock(), non_interactive=True)
        tool(task="t", role="explorer", max_steps=1)

        span_inputs = [c.args[1] for c in mock_trace.span.call_args_list]
        attempt_inputs = [i for i in span_inputs if i.get("role") == "explorer"]
        assert attempt_inputs, "expected a subagent_attempt span"
        assert all(i["parent_trace_id"] == "parent-trace-123" for i in attempt_inputs)


class TestCancelBridge:
    def test_default_is_not_cancelled(self):
        assert CancelBridge().check() is False

    def test_set_and_clear(self):
        bridge = CancelBridge()
        bridge.set_checker(lambda: True)
        assert bridge.check() is True
        bridge.set_checker(None)
        assert bridge.check() is False
