import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentnexus.observability.timeline import TimelineStore
from agentnexus.server.deps import get_runtime
from agentnexus.server.error_handlers import register_error_handlers
from agentnexus.server.routes.chat import router
from agentnexus.services.chat import ChatService
from agentnexus.services.collaboration import CollaborationRun
from agentnexus.services.subagents import SubagentRunContext
from agentnexus.agents.re_act_agent import ReActAgent
from agentnexus.tools.confirm_bridge import ConfirmBridge
from agentnexus.tools.providers.base import ToolProviderContext
from agentnexus.tools.providers.subagent_provider import SubagentToolProvider
from agentnexus.tools.registry import ToolRegistry
from tests.unit.test_collaboration_agents import model


def test_http_receipts_validate_attempt_and_retry_id(tmp_path, monkeypatch):
    store = TimelineStore(str(tmp_path / "timeline.db"))
    monkeypatch.setattr("agentnexus.observability.timeline.get_timeline_store", lambda: store)
    service = ChatService(agent_factory=lambda sid: None, memory_factory_builder=lambda sid: lambda: None)
    pool = ThreadPoolExecutor(1)
    run = CollaborationRun(SubagentRunContext(service, "s", "p"), store, pool, lambda: False)
    service._collaborations["p"] = run
    release = threading.Event()

    def execute(mailbox):
        release.wait(3)
        return {"answer": "done"}

    child = run.start("task", "explorer", "child", execute)
    app = FastAPI()
    app.include_router(router, prefix="/api")
    register_error_handlers(app)
    app.dependency_overrides[get_runtime] = lambda: SimpleNamespace(chat=service)
    path = f"/api/session/s/subagents/{child['subagent_id']}"
    payload = {"content": "new requirement", "request_id": "id", "child_run_id": child["child_run_id"]}
    try:
        with TestClient(app) as client:
            response = client.post(path + "/messages", json=payload)
            assert response.status_code == 200
            assert response.json()["delivery"] == "queued"
            assert client.post(path + "/messages", json=payload).json()["id"] == response.json()["id"]
            assert client.post(path + "/messages", json={**payload, "content": "different"}).status_code == 409
            assert client.post(path + "/messages", json={**payload, "child_run_id": "old"}).status_code == 409
            assert client.post(path + "/messages", json={**payload, "content": ""}).status_code == 422
            history = client.get(path + "/messages").json()["messages"]
            assert [m["content"] for m in history] == ["new requirement"]
            assert client.post(path + "/cancel").json()["accepted"] is True
            assert client.post(path.replace("/s/", "/other/") + "/cancel").json()["accepted"] is False
    finally:
        release.set()
        pool.shutdown()
        run.close("finished")


def test_real_websocket_streams_automatic_parent_child_exchange(tmp_path, monkeypatch):
    from agentnexus.core.config import get_settings
    from agentnexus.server import auth

    settings = get_settings()
    monkeypatch.setattr(settings, "memory_db_path", str(tmp_path / "timeline.db"))
    monkeypatch.setattr(settings, "traces_dir", str(tmp_path / "traces"))
    store = TimelineStore(settings.memory_db_path)
    monkeypatch.setattr("agentnexus.services.chat.get_timeline_store", lambda: store)
    monkeypatch.setattr("agentnexus.observability.timeline.get_timeline_store", lambda: store)
    monkeypatch.setattr(auth, "get_token", lambda: None)
    registry = ToolRegistry()
    parent, child = model(), model()
    confirmation = ConfirmBridge()
    SubagentToolProvider().register(registry, ToolProviderContext(llm_client=parent, subagent_confirm=confirmation, non_interactive=True))
    child_round = 0

    def child_think(**kwargs):
        nonlocal child_round
        child_round += 1
        child.last_tool_calls = [{"id": "ask", "name": "subagent_ask", "arguments": {"content": "What output?", "request_id": "q"}}] if child_round == 1 else []
        return "Need direction" if child_round == 1 else "Verified output"

    child.think.side_effect = child_think
    monkeypatch.setattr("agentnexus.tools.subagent._clone_llm", lambda _: child)
    service = ChatService(agent_factory=lambda _: agent, memory_factory_builder=lambda _: lambda: None, tool_executor=registry)
    agent = ReActAgent(parent, registry, max_steps=10, confirm_fn=confirmation, output=lambda *a, **kw: None)
    session = service.start_session(workspace=str(tmp_path))

    def parent_think(**kwargs):
        rows = store.list_collaboration_messages(session.id)
        context = registry.subagent_bridge.get_context()
        run = context.collaboration
        question = next((m for m in rows if m["kind"] == "question" and m["question_state"] == "open"), None)
        if not run.children:
            parent.last_tool_calls = [{"id": "start", "name": "subagent_start", "arguments": {"task": "Verify output"}}]
        elif question:
            parent.last_tool_calls = [{"id": "reply", "name": "subagent_send", "arguments": {"subagent_id": question["subagent_id"], "content": "Markdown", "question_id": question["id"], "request_id": "reply"}}]
        elif run.active:
            parent.last_tool_calls = [{"id": "wait", "name": "subagent_wait", "arguments": {"timeout": 1}}]
        else:
            parent.last_tool_calls = []
        return "Final verified output" if not parent.last_tool_calls else "Coordinate"

    parent.think.side_effect = parent_think
    runtime = SimpleNamespace(chat=service, subagent_confirm=confirmation)
    monkeypatch.setattr("agentnexus.server.app._get_runtime", lambda: runtime)
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_runtime] = lambda: runtime
    received = []
    try:
        with TestClient(app) as client:
            with client.websocket_connect(f"/api/ws/agent/{session.id}") as ws:
                ws.send_json({"type": "send_message", "content": "Verify output"})
                for _ in range(200):
                    event = ws.receive_json()
                    received.append(event)
                    if event["type"] == "done":
                        break
                else:
                    raise AssertionError("parent did not settle")
        communication = [e for e in received if e["type"] == "subagent_event" and e["kind"] == "communication"]
        assert communication
        assert received[-1]["type"] == "done"
        records = store.list_collaboration_messages(session.id)
        assert {m["kind"] for m in records} == {"question", "reply", "result"}
        assert {m["delivery"] for m in records} == {"applied"}
        assert not service._collaborations
    finally:
        registry.close()


def test_retry_can_read_a_durable_receipt_after_parent_teardown(tmp_path, monkeypatch):
    store = TimelineStore(str(tmp_path / "timeline.db"))
    monkeypatch.setattr("agentnexus.services.chat.get_timeline_store", lambda: store)
    saved = store.create_collaboration_message({"session_id": "s", "run_id": "p", "request_id": "r", "subagent_id": "child", "child_run_id": "attempt", "sender": "user", "recipient": "child", "kind": "instruction", "content": "Check tests", "question_id": ""})
    store.apply_collaboration_messages([saved["id"]], 2)
    service = ChatService(agent_factory=lambda sid: None, memory_factory_builder=lambda sid: lambda: None)
    receipt = service.send_subagent_message("s", "child", "Check tests", "r", "attempt")
    assert (receipt["id"], receipt["delivery"]) == (saved["id"], "applied")
