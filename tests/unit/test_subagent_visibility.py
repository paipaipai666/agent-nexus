"""Subagent visibility: named registry, event forwarding, per-subagent cancel."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

from agentnexus.agents.exceptions import AgentCancelled
from agentnexus.services.subagents import SubagentRegistry, SubagentRunContext
from agentnexus.tools.confirm_bridge import SubagentBridge
from agentnexus.tools.subagent import make_subagent_run


def _fake_event(etype, **payload):
    return SimpleNamespace(type=SimpleNamespace(name=etype), payload=payload)


class _StubService:
    """Mirrors ChatService._subagent_emit against a real registry; records events."""

    def __init__(self):
        self._subagents = SubagentRegistry()
        self.events = []

    def _subagent_emit(self, entry, kind, status=None, **fields):
        updates = {}
        if status is not None:
            updates["status"] = status
        if kind == "tool_call":
            updates["current_tool"] = fields.get("tool_name", "")
        if updates:
            self._subagents.update(entry, **updates)
        self.events.append({
            "subagent_id": entry.subagent_id, "name": entry.name,
            "kind": kind, "status": entry.status, **fields,
        })


class _AutoCancelContext(SubagentRunContext):
    """Cancels each entry the moment it is registered (queued-cancel path)."""

    def started(self, name_hint, role, task):
        entry = super().started(name_hint, role, task)
        entry.cancel_event.set()
        return entry


class TestSubagentRegistryNaming:
    def test_auto_name_counter_per_session_and_role(self):
        reg = SubagentRegistry()
        e1 = reg.register("s1", "r1", None, "explorer", "t")
        e2 = reg.register("s1", "r1", None, "explorer", "t")
        ex = reg.register("s1", "r1", None, "executor", "t")
        other = reg.register("s2", "r1", None, "explorer", "t")
        assert (e1.name, e2.name) == ("explorer-1", "explorer-2")
        assert ex.name == "executor-1"
        assert other.name == "explorer-1"  # counter is per-session

    def test_name_hint_sanitize_and_dedup(self):
        reg = SubagentRegistry()
        e1 = reg.register("s1", "r1", "  我的   代理  ", "explorer", "t")
        e2 = reg.register("s1", "r1", "我的 代理", "explorer", "t")
        e3 = reg.register("s1", "r1", "   ", "explorer", "t")
        assert e1.name == "我的 代理"
        assert e2.name == "我的 代理-2"
        assert e3.name == "explorer-1"  # blank hint falls back to auto

    def test_name_hint_capped_at_40_chars(self):
        reg = SubagentRegistry()
        e = reg.register("s1", "r1", "x" * 50, "explorer", "t")
        assert len(e.name) == 40

    def test_cancel_unknown_or_terminal_returns_false(self):
        reg = SubagentRegistry()
        assert reg.cancel("nope") is False
        e = reg.register("s1", "r1", None, "explorer", "t")
        reg.update(e, status="completed")
        assert reg.cancel(e.subagent_id) is False
        live = reg.register("s1", "r1", None, "explorer", "t")
        assert reg.cancel(live.subagent_id) is True
        assert live.cancel_event.is_set()


class TestSubagentVisibilityEvents:
    def _tool_with_bridge(self, service, ctx_cls=SubagentRunContext):
        bridge = SubagentBridge()
        bridge.set_context(ctx_cls(service, "session_1", "run_1"))
        return make_subagent_run(
            parent_llm=MagicMock(), non_interactive=True, subagent_bridge=bridge,
        )

    def test_run_forwards_child_events_and_payload_carries_identity(self, monkeypatch):
        service = _StubService()
        monkeypatch.setattr("agentnexus.tools.subagent._clone_llm", lambda _p: MagicMock())

        def fake_run(self, question, memory_manager=None):
            self._on_event(_fake_event(
                "TOOL_START", name="file_read", arguments={"path": "a"}, id="c1"), None, None)
            self._on_event(_fake_event("STREAM_TOKEN", token="正在"), None, None)
            self._on_event(_fake_event("TOOLS_REQUESTED", thought="先看看文件"), None, None)
            self._on_event(_fake_event(
                "TOOL_DONE", name="file_read", result="ok", id="c1", duration_ms=5), None, None)
            return SimpleNamespace(answer="子任务结论", steps=[object()])

        monkeypatch.setattr("agentnexus.tools.subagent.ReActAgent.run", fake_run)

        tool = self._tool_with_bridge(service)
        payload = json.loads(tool(task="总结 README", name="调研A"))

        kinds = [e["kind"] for e in service.events]
        assert kinds[0] == "started"
        assert kinds[-1] == "finished"
        for k in ("tool_call", "tool_result", "token", "thinking"):
            assert k in kinds
        started = service.events[0]
        assert started["status"] == "thinking"
        assert started["name"] == "调研A"
        tool_call = next(e for e in service.events if e["kind"] == "tool_call")
        assert tool_call["status"] == "tool_calling"
        assert tool_call["tool_name"] == "file_read"
        assert service.events[-1]["status"] == "completed"

        assert payload["status"] == "ok"
        assert payload["name"] == "调研A"
        assert payload["subagent_id"].startswith("sub_")
        entry = service._subagents.get(payload["subagent_id"])
        assert entry.name == "调研A"
        assert entry.status == "completed"
        assert entry.finished_at is not None

    def test_cancel_before_start_skips_child_and_marks_interrupted(self, monkeypatch):
        service = _StubService()
        monkeypatch.setattr("agentnexus.tools.subagent._clone_llm", lambda _p: MagicMock())
        runs = []

        def fake_run(self, question, memory_manager=None):
            runs.append(1)
            return SimpleNamespace(answer="x", steps=[])

        monkeypatch.setattr("agentnexus.tools.subagent.ReActAgent.run", fake_run)

        tool = self._tool_with_bridge(service, ctx_cls=_AutoCancelContext)
        payload = json.loads(tool(task="queued task"))

        assert runs == []  # child never started
        assert "打断" in payload["summary"]
        assert service.events[-1]["status"] == "interrupted"
        entry = service._subagents.get(payload["subagent_id"])
        assert entry.status == "interrupted"

    def test_cancel_mid_run_short_circuits_without_fallback(self, monkeypatch):
        service = _StubService()
        monkeypatch.setattr("agentnexus.tools.subagent._clone_llm", lambda _p: MagicMock())
        runs = []

        def fake_run(self, question, memory_manager=None):
            runs.append(1)
            raise AgentCancelled("cancelled")

        monkeypatch.setattr("agentnexus.tools.subagent.ReActAgent.run", fake_run)

        tool = self._tool_with_bridge(service)
        payload = json.loads(tool(task="slow task"))

        assert len(runs) == 1  # no explorer fallback retry after cancel
        assert payload["status"] == "error"
        assert "打断" in payload["summary"]
        assert service.events[-1]["kind"] == "finished"
        assert service.events[-1]["status"] == "interrupted"
        entry = service._subagents.get(payload["subagent_id"])
        assert entry.status == "interrupted"

    def test_no_bridge_preserves_legacy_behavior(self, monkeypatch):
        monkeypatch.setattr("agentnexus.tools.subagent._clone_llm", lambda _p: MagicMock())
        monkeypatch.setattr(
            "agentnexus.tools.subagent.ReActAgent.run",
            lambda self, question, memory_manager=None: SimpleNamespace(answer="ok", steps=[]),
        )

        tool = make_subagent_run(parent_llm=MagicMock(), non_interactive=True)
        payload = json.loads(tool(task="t"))

        assert payload["status"] == "ok"
        assert payload["subagent_id"] == ""
        assert payload["name"] == ""


class TestSubagentEventPersistence:
    """token/reasoning subagent events are live-only (timeline skip)."""

    def _service(self, monkeypatch):
        from agentnexus.services.chat import ChatService
        from agentnexus.services.subagents import SubagentEntry

        service = ChatService(agent_factory=lambda _sid: MagicMock(),
                              memory_factory_builder=lambda _sid: (lambda: MagicMock()))
        recorded = []
        monkeypatch.setattr(
            "agentnexus.services.chat.get_timeline_store",
            lambda: SimpleNamespace(record_event=lambda *a, **k: recorded.append((a, k))),
        )
        entry = SubagentEntry(subagent_id="sub_x", session_id="s1", run_id="r1",
                              name="n", role="explorer", task="t")
        service._subagents.register = lambda *a, **k: entry  # type: ignore[assignment]
        return service, entry, recorded

    def test_token_events_not_persisted_but_tool_events_are(self, monkeypatch):
        service, entry, recorded = self._service(monkeypatch)
        service._subagent_emit(entry, "token", content="x")
        service._subagent_emit(entry, "reasoning", content="y")
        service._subagent_emit(entry, "tool_call", status="tool_calling",
                               tool_name="file_read")
        persisted_types = [a[3] for a, _k in recorded]
        assert persisted_types == ["subagent_event"]  # only the tool_call

    def test_events_reach_live_queues_with_seq(self, monkeypatch):
        import queue as _queue
        service, entry, _recorded = self._service(monkeypatch)
        q = _queue.Queue()
        service._run_events["r1"] = q
        service._subagent_emit(entry, "token", content="x")
        event = q.get_nowait()
        assert event.type == "subagent_event"
        assert event.payload["kind"] == "token"
        assert event.seq == 1  # seq still assigned for live ordering
