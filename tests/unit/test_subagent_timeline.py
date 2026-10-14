import contextvars
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentnexus.observability import timeline
from agentnexus.observability.timeline import TimelineStore
from agentnexus.server.error_handlers import register_error_handlers
from agentnexus.server.routes.chat import router
from agentnexus.services.chat import ChatService
from agentnexus.services.subagents import SubagentRunContext
from agentnexus.tools.confirm_bridge import SubagentBridge
from agentnexus.tools.subagent import make_subagent_run
from agentnexus.tools.workspace import current_workspace


@pytest.fixture
def timeline_api(tmp_path, monkeypatch):
    store = TimelineStore(str(tmp_path / "timeline.db"))
    monkeypatch.setattr(timeline, "get_timeline_store", lambda: store)
    monkeypatch.setattr("agentnexus.services.chat.get_timeline_store", lambda: store)
    app = FastAPI()
    app.include_router(router, prefix="/api")
    register_error_handlers(app)
    with TestClient(app) as client:
        yield client, store
    store._conn.close()


def test_timeline_scope_filters_before_pagination(timeline_api):
    client, store = timeline_api
    store.record_event("s1", "parent", 1, "subagent_event", {
        "subagent_id": "sub_a", "kind": "tool_call", "tool_name": "file_read",
        "arguments": {"path": "a.txt"},
    })
    store.record_event("s1", "parent", 2, "subagent_event", {
        "subagent_id": "sub_b", "kind": "thinking", "content": "Read B",
    })
    store.record_event("s1", "parent", 3, "tool_start", {"name": "subagent_run"})
    store.record_event("s1", "parent", 4, "subagent_event", {
        "subagent_id": "sub_a", "kind": "tool_result", "tool_name": "file_read",
        "result": "A contents", "duration_ms": 5,
    })
    store.record_event("s2", "other", 1, "subagent_event", {
        "subagent_id": "sub_a", "kind": "thinking", "content": "Other session",
    })

    main = client.get("/api/session/s1/events", params={"agent_id": "main", "limit": 1}).json()
    assert [e["event_type"] for e in main["events"]] == ["tool_start"]
    assert main["events"][0]["payload"] == {"name": "subagent_run"}
    assert main["last_id"] == 3

    first = client.get("/api/session/s1/events", params={"agent_id": "sub_a", "limit": 1}).json()
    assert [e["event_type"] for e in first["events"]] == ["tool_start"]
    assert first["events"][0]["payload"]["name"] == "file_read"
    assert first["last_id"] == 1
    second = client.get("/api/session/s1/events", params={
        "agent_id": "sub_a", "after": first["last_id"], "limit": 1,
    }).json()
    assert [e["event_type"] for e in second["events"]] == ["tool_done"]
    assert second["events"][0]["payload"]["result"] == "A contents"
    assert second["last_id"] == 4
    assert client.get("/api/session/s1/events", params={
        "agent_id": "sub_a", "after": second["last_id"],
    }).json()["events"] == []
    assert client.get("/api/session/s1/events", params={"agent_id": "sub_b"}).json()["events"][0]["payload"]["thought"] == "Read B"
    assert len(client.get("/api/session/s1/events").json()["events"]) == 4
    trajectory = client.get("/api/session/s1/events", params={
        "agent_id": "sub_a", "view": "trajectory",
    }).json()["events"]
    assert [e["event_type"] for e in trajectory] == ["subagent_event", "subagent_event"]
    assert [e["payload"]["kind"] for e in trajectory] == ["tool_call", "tool_result"]



def _scripted_llm(answer, error=""):
    llm = MagicMock()
    llm.model = "test/model"
    llm.last_error = error
    llm.last_truncated = False
    llm.last_tool_calls = []
    llm.last_reasoning_content = ""
    llm.last_usage = {"input_tokens": 10, "output_tokens": 5}
    llm.capabilities.supports_thinking = False
    llm.capabilities.supports_tool_calling = True
    llm.capabilities.supports_json_mode = True
    llm.capabilities.supports_json_schema = False
    llm.capabilities.supports_parallel_tool_calls = False
    llm.think.return_value = answer
    return llm


def test_real_child_runtime_records_context_for_each_retry(timeline_api, monkeypatch):
    client, store = timeline_api
    models = iter([
        _scripted_llm("", "first attempt failed"),
        _scripted_llm("A answer"),
        _scripted_llm("B answer"),
    ])
    monkeypatch.setattr("agentnexus.tools.subagent._clone_llm", lambda _parent: next(models))
    service = ChatService(
        agent_factory=lambda _sid: MagicMock(),
        memory_factory_builder=lambda _sid: lambda: MagicMock(),
    )
    bridge = SubagentBridge()
    bridge.set_context(SubagentRunContext(service, "s1", "parent"))
    tool = make_subagent_run(non_interactive=True, subagent_bridge=bridge)
    store.record_snapshot("s1", "parent", 1, [{"role": "user", "content": "Parent only"}])

    a = json.loads(tool(task="Task A", name="Agent A"))
    b = json.loads(tool(task="Task B", name="Agent B"))
    assert a["answer"] == "A answer"
    assert b["answer"] == "B answer"
    events_a = client.get("/api/session/s1/events", params={"agent_id": a["subagent_id"]}).json()["events"]
    calls_a = [e for e in events_a if e["event_type"] == "turn_journal" and e["payload"]["event"] == "LLM_CALL"]
    assert len(calls_a) == 2
    assert calls_a[0]["payload"]["error"] == "first attempt failed"
    assert calls_a[1]["payload"]["model"] == "test/model"
    assert calls_a[1]["payload"]["input_tokens"] == 10
    assert calls_a[0]["run_id"] != calls_a[1]["run_id"]
    assert [e["step_id"] for e in calls_a] == [1, 1]
    snapshots = [client.get(
        f"/api/session/s1/context/{e['run_id']}/{e['step_id']}",
        params={"agent_id": a["subagent_id"]},
    ).json() for e in calls_a]
    assert "Task A" in snapshots[0]["messages"][-1]["content"]
    assert "empty_answer" not in snapshots[0]["messages"][-1]["content"]
    assert "empty_answer" in snapshots[1]["messages"][-1]["content"]
    assert store.get_snapshot("s1", "parent", 1)["messages"] == [{"role": "user", "content": "Parent only"}]
    assert client.get("/api/session/s1/events", params={"agent_id": "main"}).json()["events"] == []
    events_b = client.get("/api/session/s1/events", params={"agent_id": b["subagent_id"]}).json()["events"]
    calls_b = [e for e in events_b if e["event_type"] == "turn_journal" and e["payload"]["event"] == "LLM_CALL"]
    assert len(calls_b) == 1
    assert calls_b[0]["run_id"] not in {e["run_id"] for e in calls_a}


def test_context_lookup_checks_agent_and_session_ownership(timeline_api):
    client, store = timeline_api
    store.record_event("s1", "parent", 1, "run_started", {})
    store.record_snapshot("s1", "parent", 1, [{"role": "user", "content": "Parent"}])
    store.record_event("s1", "parent", 2, "subagent_event", {
        "subagent_id": "sub_a", "kind": "runtime", "child_run_id": "child_a",
        "event_type": "turn_journal", "event_payload": {"event": "LLM_CALL"}, "step_id": 1,
    })
    store.record_snapshot("s1", "child_a", 1, [{"role": "user", "content": "A only"}])
    own = client.get("/api/session/s1/context/child_a/1", params={"agent_id": "sub_a"})
    assert own.status_code == 200
    assert own.json()["messages"] == [{"role": "user", "content": "A only"}]
    for path, agent_id in [
        ("/api/session/s1/context/child_a/1", "sub_b"),
        ("/api/session/s1/context/parent/1", "sub_a"),
        ("/api/session/s1/context/child_a/1", "main"),
        ("/api/session/s2/context/child_a/1", "sub_a"),
    ]:
        assert client.get(path, params={"agent_id": agent_id}).status_code == 404


def test_parallel_children_keep_separate_timelines_after_reopening_store(timeline_api, monkeypatch):
    client, store = timeline_api
    barrier = Barrier(2)

    def clone(_parent):
        llm = _scripted_llm("Child answer")

        def think(**_kwargs):
            barrier.wait(timeout=5)
            return "Child answer"

        llm.think.side_effect = think
        return llm

    monkeypatch.setattr("agentnexus.tools.subagent._clone_llm", clone)
    service = ChatService(
        agent_factory=lambda _sid: MagicMock(),
        memory_factory_builder=lambda _sid: lambda: MagicMock(),
    )
    bridge = SubagentBridge()
    bridge.set_context(SubagentRunContext(service, "s1", "parent"))
    tool = make_subagent_run(non_interactive=True, subagent_bridge=bridge)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(contextvars.copy_context().run, tool, task=f"Parallel {name}", name=name) for name in ("A", "B")]
        children = [json.loads(future.result(timeout=10)) for future in futures]
    reopened = TimelineStore(store._db_path)
    monkeypatch.setattr(timeline, "get_timeline_store", lambda: reopened)
    try:
        runs = set()
        for child, name in zip(children, ("A", "B"), strict=True):
            assert child["answer"] == "Child answer"
            events = client.get("/api/session/s1/events", params={"agent_id": child["subagent_id"]}).json()["events"]
            calls = [e for e in events if e["event_type"] == "turn_journal" and e["payload"]["event"] == "LLM_CALL"]
            assert len(calls) == 1
            runs.add(calls[0]["run_id"])
            snapshot = client.get(f"/api/session/s1/context/{calls[0]['run_id']}/1", params={"agent_id": child["subagent_id"]}).json()
            assert f"Parallel {name}" in snapshot["messages"][-1]["content"]
            assert any(e["payload"].get("name") == name for e in events)
            assert not any(e["event_type"] == "subagent_event" for e in events)
        assert len(runs) == 2
    finally:
        reopened._conn.close()


def test_child_tool_and_stream_events_have_readable_timeline_nodes(timeline_api, monkeypatch):
    client, store = timeline_api
    source = Path(store._db_path).with_name("module.txt")
    source.write_text("Module body", encoding="utf-8")
    llm = _scripted_llm("Child result")
    calls = 0

    def think(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            llm.last_tool_calls = [{"id": "read_call", "name": "file_read", "arguments": {"path": str(source)}}]
            kwargs["on_token"]("Inspect file", False)
            return "Inspect file"
        llm.last_tool_calls = []
        kwargs["on_token"]("Child result", False)
        return "Child result"

    llm.think.side_effect = think
    monkeypatch.setattr("agentnexus.tools.subagent._clone_llm", lambda _parent: llm)
    service = ChatService(
        agent_factory=lambda _sid: MagicMock(),
        memory_factory_builder=lambda _sid: lambda: MagicMock(),
    )
    bridge = SubagentBridge()
    bridge.set_context(SubagentRunContext(service, "s1", "parent"))
    token = current_workspace.set(str(source.parent))
    try:
        child = json.loads(make_subagent_run(non_interactive=True, subagent_bridge=bridge)(task="Read module"))
    finally:
        current_workspace.reset(token)
    assert child["answer"] == "Child result"
    events = client.get("/api/session/s1/events", params={"agent_id": child["subagent_id"]}).json()["events"]
    tools = [e for e in events if e["event_type"] in ("tool_start", "tool_done")]
    assert [e["event_type"] for e in tools] == ["tool_start", "tool_done"]
    assert [e["step_id"] for e in tools] == [1, 1]
    assert tools[0]["payload"]["name"] == "file_read"
    assert "Module body" in tools[1]["payload"]["result"]
    assert any(e["event_type"] == "token_summary" and e["payload"]["content_chars"] == 12 for e in events)
    assert not any(e["payload"].get("kind") in ("token", "reasoning") for e in store.list_events("s1"))
