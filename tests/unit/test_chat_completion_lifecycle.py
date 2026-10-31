"""Final answers keep their completed outcome during optional memory processing."""

import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentnexus.agents.re_act_agent import ReActAgent
from agentnexus.core.capabilities import ModelCapabilities, SessionCapabilityTracker
from agentnexus.core.config import get_settings
from agentnexus.memory.manager import MemoryManager
from agentnexus.memory.versioned import ConversationVersionManager
from agentnexus.services.chat import ChatService
from agentnexus.tools.confirm_bridge import ConfirmBridge
from agentnexus.tools.registry import ToolRegistry


@dataclass
class AnswerModel:
    model: str = "completion-fixture"
    base_url: str = ""
    capabilities: ModelCapabilities = field(default_factory=lambda: ModelCapabilities(supports_tool_calling=True))
    session_tracker: SessionCapabilityTracker = field(default_factory=SessionCapabilityTracker)
    last_error: str = ""
    last_reasoning_content: str = ""
    last_tool_calls: list = field(default_factory=list)
    last_usage: dict = field(default_factory=dict)
    last_truncated: bool = False

    def think(self, **kwargs):
        return "Work completed"


def completion_service(tmp_path):
    agent = ReActAgent(AnswerModel(), ToolRegistry(), output=lambda _: None)
    service = ChatService(lambda _: agent, lambda _: lambda: memory)
    session = service.start_session(workspace=str(tmp_path))
    memory = MemoryManager(session.id, enable_long_term=False)
    version = ConversationVersionManager(session.id, get_settings().memory_db_path, workspace_path=str(tmp_path))
    service.attach_session(session, agent=agent, memory=memory, version=version)
    return service, session, memory, version


def test_disconnect_after_final_persistence_preserves_completed_answer(tmp_path):
    service, session, memory, version = completion_service(tmp_path)
    entered, release = threading.Event(), threading.Event()
    runs = []

    def slow_conclusion(*_):
        entered.set()
        assert release.wait(timeout=5)

    memory.conclude = slow_conclusion
    with ThreadPoolExecutor(max_workers=1) as worker:
        sending = worker.submit(service.send_message, session.id, "Finish work", on_run_started=lambda run: runs.append(run.id))
        try:
            assert entered.wait(timeout=3)
            assert service.get_run_snapshot(runs[0]).status == "finished"
            assert service.get_run_recovery(runs[0])["persisted"] is True
            service.cancel_run(runs[0], reason="client_disconnected")
        finally:
            release.set()
        run = sending.result(timeout=3)
    recovery = service.get_run_recovery(run.id)
    assert recovery["status"] == "finished"
    assert recovery["answer"] == "Work completed"
    assert recovery["save_error"] == ""
    events = list(service.stream_events(run.id))
    assert [event.type for event in events if event.type in {"run_finished", "run_interrupted", "run_failed"}] == ["run_finished"]
    messages = version.get_messages(limit=0)
    assert sum(message["content"] == "[最终答案] Work completed" for message in messages) == 1


def test_optional_memory_submission_failure_does_not_fail_the_completed_task(tmp_path):
    service, session, memory, _ = completion_service(tmp_path)

    def broken_conclusion(*_):
        raise RuntimeError("Memory worker unavailable")

    memory.conclude = broken_conclusion
    run = service.send_message(session.id, "Finish work")
    recovery = service.get_run_recovery(run.id)
    assert recovery["status"] == "finished"
    assert recovery["answer"] == "Work completed"
    assert recovery["persisted"] is True
    assert [event.type for event in service.stream_events(run.id) if event.type in {"run_finished", "run_failed"}] == ["run_finished"]


def test_websocket_can_finish_and_disconnect_while_memory_extraction_is_pending(tmp_path, monkeypatch, caplog):
    from agentnexus.server.routes.chat import router

    service, session, _, version = completion_service(tmp_path)
    entered, release = threading.Event(), threading.Event()

    class ExtractModel:
        def think(self, *_args, **_kwargs):
            entered.set()
            assert release.wait(timeout=5)
            return "{}"

    get_settings().memory_llm_gate = False
    memory = MemoryManager(session.id, llm=ExtractModel())
    service.attach_session(session, memory=memory)
    runtime = SimpleNamespace(chat=service, subagent_confirm=ConfirmBridge())
    monkeypatch.setattr("agentnexus.server.app._get_runtime", lambda: runtime)
    monkeypatch.setattr("agentnexus.server.auth.get_token", lambda: None)
    app = FastAPI()
    app.include_router(router, prefix="/api")
    try:
        with TestClient(app) as client:
            with client.websocket_connect(f"/api/ws/agent/{session.id}") as ws:
                ws.send_json({"type": "send_message", "content": "记住蓝色主题偏好"})
                events = []
                while not any(event.get("type") == "done" for event in events):
                    event = ws.receive_json()
                    assert event.get("type") != "error", event
                    events.append(event)
                finished = next(event for event in events if event["type"] == "answer")
                assert finished["answer"] == "Work completed"
                assert entered.wait(timeout=3)
                assert release.is_set() is False
                run_id = finished["run_id"]
            recovery = service.get_run_recovery(run_id)
            assert recovery["status"] == "finished"
            assert recovery["persisted"] is True
            assert recovery["reason"] == ""
            assert recovery["save_error"] == ""
    finally:
        release.set()
        memory.conclude("", "", allow_memory=False).result(timeout=3)
    assert sum(message["content"] == "[最终答案] Work completed" for message in version.get_messages(limit=0)) == 1
    assert not [record for record in caplog.records if "commit rejected" in record.getMessage()]


def test_runtime_stops_optional_memory_jobs_before_closing_providers(tmp_path):
    from agentnexus.app.runtime import AppRuntime

    service, session, _, version = completion_service(tmp_path)
    entered, release = threading.Event(), threading.Event()

    class ExtractModel:
        def think(self, *_args, **_kwargs):
            entered.set()
            assert release.wait(timeout=5)
            return "{}"

    get_settings().memory_llm_gate = False
    memory = MemoryManager(session.id, llm=ExtractModel())
    service.attach_session(session, memory=memory)
    first = memory.conclude("记住第一个偏好", "已记住")
    queued = None
    try:
        assert entered.wait(timeout=3)
        queued = memory.conclude("记住第二个偏好", "已记住")

        def close_providers():
            assert queued.cancelled() is True

        runtime = AppRuntime(
            settings=get_settings(), llm=None, executor=None, agent=None,
            memory_manager=memory, version_manager=version, mcp_manager=None,
            capability_runtime=None, chat=service, skill=None, subagent_confirm=None,
            session_id=session.id, modules=SimpleNamespace(close=close_providers),
        )
        runtime.close()
        assert memory.conclude("记住第三个偏好", "已记住").cancelled() is True
    finally:
        release.set()
        first.result(timeout=3)
