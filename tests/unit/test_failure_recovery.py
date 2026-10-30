"""Recovery contracts exercised through the HTTP and turn lifecycle interfaces."""

from concurrent.futures import ThreadPoolExecutor, TimeoutError
from threading import Event
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentnexus.core.config import get_settings
from agentnexus.memory.short_term import ShortTermMemory
from agentnexus.memory.versioned import ConversationVersionManager
from agentnexus.server.deps import get_runtime
from agentnexus.server.error_handlers import register_error_handlers
from agentnexus.server.routes.chat import _map_to_gui_event
from agentnexus.server.routes.chat import router as chat_router
from agentnexus.server.routes.version import router
from agentnexus.services.chat import AgentEvent, ChatService
from agentnexus.services.turn import TurnRuntime


@pytest.fixture
def version_client(monkeypatch):
    monkeypatch.setattr("agentnexus.memory.short_term.get_token_encoding", lambda: SimpleNamespace(encode=str.encode))
    memories = {}

    def memory_factory(session_id):
        def create():
            memories[session_id] = SimpleNamespace(short_term=ShortTermMemory())
            return memories[session_id]
        return create

    service = ChatService(lambda _: None, memory_factory)
    sessions = [service.start_session().id for _ in range(2)]
    versions = []
    for session_id in sessions:
        version = ConversationVersionManager(session_id, get_settings().memory_db_path)
        version.commit_with_messages([{"role": "user", "content": f"{session_id} first"}], question="first")
        version.commit_with_messages([{"role": "user", "content": f"{session_id} second"}], question="second")
        service.ensure_session_restored(session_id)
        versions.append(version)
    runtime = SimpleNamespace(chat=service, version_manager=versions[0], memory_manager=memories[sessions[0]])
    monkeypatch.setattr("agentnexus.server.app._get_runtime", lambda: runtime)
    app = FastAPI()
    app.dependency_overrides[get_runtime] = lambda: runtime
    register_error_handlers(app)
    app.include_router(router, prefix="/api/version")
    app.include_router(chat_router, prefix="/api")
    with TestClient(app) as client:
        yield client, service, sessions, versions, memories
    for version in [*versions, *service._version_managers.values()]:
        version._conn.close()


def test_undo_targets_requested_session_and_restores_its_memory(version_client):
    client, _, (a, b), (version_a, _), memories = version_client
    response = client.post(f"/api/version/undo?session_id={b}")
    assert response.status_code == 200
    assert response.json()["checkpoint"]["session_id"] == b
    assert client.get(f"/api/version/status?session_id={b}").json()["head"]["question"] == "first"
    assert version_a.status()["head"]["question"] == "second"
    assert [m["content"] for m in memories[b].short_term.get_all()] == [f"{b} first"]
    assert [m["content"] for m in memories[a].short_term.get_all()] == [f"{a} first", f"{a} second"]


def test_version_requires_known_session_and_blocks_changes_during_execution(version_client):
    client, service, (a, _), _, _ = version_client
    assert client.get("/api/version/status?session_id=missing").status_code == 404
    service.mark_processing(True, session_id=a)
    response = client.post(f"/api/version/undo?session_id={a}")
    assert response.status_code == 409
    assert client.get(f"/api/version/status?session_id={a}").json()["head"]["question"] == "second"


def test_redo_reset_and_jump_stay_in_the_requested_session(version_client):
    client, _, (a, b), (version_a, version_b), memories = version_client
    target = version_b.log()[1]["id"]
    assert client.post(f"/api/version/jump?session_id={b}&cp_id={target}").status_code == 200
    assert client.post(f"/api/version/jump?session_id={b}&cp_id={version_a.log()[0]['id']}").status_code == 404
    assert client.post(f"/api/version/redo?session_id={b}").json()["checkpoint"]["question"] == "second"
    assert client.post(f"/api/version/reset?session_id={b}").status_code == 200
    assert client.get(f"/api/version/status?session_id={b}").json()["head"] is None
    assert memories[b].short_term.get_all() == []
    assert client.get(f"/api/version/log?session_id={a}").json()["total"] == 2


def test_failed_final_save_can_retry_without_losing_or_duplicating_messages(monkeypatch):
    monkeypatch.setattr("agentnexus.memory.short_term.get_token_encoding", lambda: SimpleNamespace(encode=str.encode))
    version = ConversationVersionManager("save-retry", get_settings().memory_db_path)
    memory = SimpleNamespace(short_term=ShortTermMemory())
    version.commit_with_messages([{"role": "user", "content": "work"}])
    turn = TurnRuntime(run_id="save-run", session_id="save-retry", question="work", memory_manager=memory, version_manager=version)
    memory.short_term.append("user", "work")
    memory.short_term.append("assistant", "result")
    commit = version.commit_with_messages
    monkeypatch.setattr(version, "commit_with_messages", lambda **_: (_ for _ in ()).throw(OSError("disk full")))
    record = turn.finish("result")
    assert record.answer == "result"
    assert turn.persist_snapshot() is False
    monkeypatch.setattr(version, "commit_with_messages", commit)
    assert turn.persist_snapshot() is True
    assert turn.persist_snapshot() is True
    assert [m["content"] for m in version.get_messages()] == ["work", "result"]
    assert version.log()[0]["answer"] == "result"
    assert turn.last_commit_error == ""
    version._conn.close()


def test_chat_reports_unsaved_result_instead_of_persisted_success(monkeypatch):
    agent = MagicMock()
    agent.run.return_value = "useful result"
    memory = MagicMock()
    memory.short_term.get_all.return_value = []
    monkeypatch.setattr(ConversationVersionManager, "commit_with_messages", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    service = ChatService(lambda _: agent, lambda _: lambda: memory)
    session = service.start_session()
    run = service.send_message(session.id, "work")
    events = list(service.stream_events(run.id))
    terminal = next(event for event in events if event.type == "run_finished")
    assert terminal.payload["answer"] == "useful result"
    assert [event.type for event in events][-1] == "run_save_failed"
    assert events[-1].payload["persisted"] is False


def test_checkpoint_failure_rolls_back_message_write_before_retry(monkeypatch):
    version = ConversationVersionManager("atomic-retry", get_settings().memory_db_path)
    version._conn.execute("CREATE TRIGGER fail_checkpoint BEFORE INSERT ON conversation_checkpoints BEGIN SELECT RAISE(ABORT, 'disk failure'); END")
    with pytest.raises(Exception, match="disk failure"):
        version.commit_with_messages([{"role": "assistant", "content": "retained result"}], answer="retained result")
    version._conn.execute("DROP TRIGGER fail_checkpoint")
    version.commit_with_messages([{"role": "assistant", "content": "retained result"}], answer="retained result")
    assert [m["content"] for m in version.get_messages()] == ["retained result"]
    version._conn.close()


def test_missing_restore_does_not_create_another_session(version_client):
    client, service, sessions, _, _ = version_client
    response = client.post("/api/session/restore", json={"skill": "missing"})
    assert response.status_code == 404
    assert sorted(service.get_session_snapshot(sid)["session"].id for sid in sessions) == sorted(sessions)
    assert len(service._sessions) == 2


def test_duplicate_submission_survives_restart_without_executing_again():
    outputs = []
    agent = MagicMock()
    agent.run.side_effect = lambda *a, **k: outputs.append("one execution") or "completed once"
    service = ChatService(lambda _: agent, lambda _: lambda: MagicMock())
    session = service.start_session()
    first = service.send_message(session.id, "work", client_message_id="request-one")
    repeated = service.send_message(session.id, "work", client_message_id="request-one")
    restarted = ChatService(lambda _: agent, lambda _: lambda: MagicMock())
    restarted.ensure_session_restored(session.id)
    after_restart = restarted.send_message(session.id, "work", client_message_id="request-one")
    assert first.id == repeated.id == after_restart.id
    assert outputs == ["one execution"]
    assert restarted.get_run_recovery(first.id)["answer"] == "completed once"
    assert restarted.get_run_recovery(first.id)["persisted"] is True
    with pytest.raises(ValueError, match="提交 ID"):
        restarted.send_message(session.id, "different request", client_message_id="request-one")


def test_terminal_event_preserves_stop_reason_answer_and_activity():
    event = AgentEvent("run_interrupted", {"status": "interrupted", "reason": "client_disconnected", "answer": "partial work", "journal": ["tool done: file_write -> saved"]}, run_id="interrupted-run")
    result = _map_to_gui_event(event, None, 5)
    assert result["status"] == "interrupted"
    assert result["reason"] == "client_disconnected"
    assert result["answer"] == "partial work"
    assert result["journal"] == ["tool done: file_write -> saved"]


def test_stop_request_before_run_id_prevents_execution():
    executed = []
    agent = MagicMock()
    agent.run.side_effect = lambda *a, **k: executed.append("executed") or "done"
    service = ChatService(lambda _: agent, lambda _: lambda: MagicMock())
    session = service.start_session()
    response = service.request_submission_cancel(session.id, "cancel-before-ack")
    assert response["status"] == "requested"
    from agentnexus.core.exceptions import AgentCancelled
    with pytest.raises(AgentCancelled):
        service.send_message(session.id, "work", client_message_id="cancel-before-ack")
    assert executed == []
    receipt = service.get_submission_receipt(session.id, "cancel-before-ack")
    assert receipt["recovery"]["status"] == "interrupted"
    assert receipt["recovery"]["reason"] == "cancelled"


def test_redo_survives_reopening_and_a_new_branch_invalidates_it():
    path = get_settings().memory_db_path
    version = ConversationVersionManager("durable-redo", path)
    version.commit(question="first", answer="a")
    version.commit(question="second", answer="b")
    version.commit(question="third", answer="c")
    version.undo()
    version.undo()
    reopened = ConversationVersionManager("durable-redo", path)
    assert reopened.status()["can_redo"] is True
    assert reopened.redo()["question"] == "second"
    assert version.redo()["question"] == "third"
    reopened.undo()
    version.commit(question="new branch", answer="d")
    assert reopened.status()["head"]["question"] == "new branch"
    assert reopened.status()["can_redo"] is False
    reopened._conn.close()
    version._conn.close()


def test_execution_timeout_explains_recovery_without_blind_retry():
    agent = MagicMock()
    agent.run.side_effect = TimeoutError("provider request timed out")
    service = ChatService(lambda _: agent, lambda _: lambda: MagicMock())
    session = service.start_session()
    runs = []
    with pytest.raises(TimeoutError):
        service.send_message(session.id, "work", on_run_started=runs.append)
    recovery = service.get_run_recovery(runs[0].id)
    assert recovery["failure_code"] == "provider_timeout"
    assert "核对" in recovery["next_action"]
    assert "超时" in recovery["reason"]


def test_final_answer_is_saved_without_a_memory_manager():
    version = ConversationVersionManager("memory-free", get_settings().memory_db_path)
    turn = TurnRuntime(run_id="memory-free-run", session_id="memory-free", question="work", version_manager=version)
    turn.finish("saved answer")
    assert turn.persist_snapshot() is True
    assert version.log()[0]["answer"] == "saved answer"
    version._conn.close()


def test_recovery_record_write_failure_is_not_reported_as_saved(monkeypatch):
    from agentnexus.storage.timeline import TimelineStore

    agent = MagicMock()
    agent.run.return_value = "retained result"
    service = ChatService(lambda _: agent, lambda _: lambda: MagicMock())
    session = service.start_session()
    save_record = TimelineStore.save_run_record
    monkeypatch.setattr(TimelineStore, "save_run_record", lambda *a, **k: (_ for _ in ()).throw(OSError("record disk full")))
    run = service.send_message(session.id, "work")
    events = list(service.stream_events(run.id))
    assert events[-1].type == "run_save_failed"
    assert service.get_run_recovery(run.id)["persisted"] is False
    checkpoint_count = len(service.session_version(session.id).log())
    monkeypatch.setattr(TimelineStore, "save_run_record", save_record)
    assert service.retry_run_save(run.id)["persisted"] is True
    assert len(service.session_version(session.id).log()) == checkpoint_count


def test_cancellation_serializes_with_an_inflight_round_commit(monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    monkeypatch.setattr("agentnexus.memory.short_term.get_token_encoding", lambda: SimpleNamespace(encode=str.encode))
    version = ConversationVersionManager("cancel-round", get_settings().memory_db_path)
    version.commit_with_messages([{"role": "user", "content": "work"}])
    memory = SimpleNamespace(short_term=ShortTermMemory())
    turn = TurnRuntime(run_id="cancel-round-run", session_id="cancel-round", question="work", memory_manager=memory, version_manager=version)
    memory.short_term.append("assistant", "completed step")
    entered = threading.Event()
    release = threading.Event()
    cancellation_writing = threading.Event()
    commit = version.commit_with_messages

    def delayed_commit(**kwargs):
        if kwargs["answer"] == "":
            entered.set()
            assert release.wait(timeout=2)
        else:
            cancellation_writing.set()
        return commit(**kwargs)

    monkeypatch.setattr(version, "commit_with_messages", delayed_commit)
    with ThreadPoolExecutor(max_workers=2) as executor:
        round_commit = executor.submit(turn.commit_round)
        assert entered.wait(timeout=2)
        cancelled = executor.submit(turn.cancel, "user_cancelled")
        cancellation_writing.wait(timeout=0.3)
        release.set()
        assert round_commit.result(timeout=2) is True
        assert cancelled.result(timeout=2).status == "interrupted"
    assert [m["content"] for m in version.get_messages()] == ["work", "completed step", "[已取消] user_cancelled"]
    assert "已中断" in version.log()[0]["answer"]
    version._conn.close()


def test_recovery_includes_activity_that_finishes_after_stop_was_requested():
    service = ChatService(lambda _: None, lambda _: lambda: MagicMock())
    session = service.start_session()
    run, _, turn = service.begin_turn(session.id, "work", memory_manager=MagicMock())
    service.cancel_run(run.id, "user_cancelled")
    turn.record("tool done", "file_write -> saved before tool returned")
    recovery = service.get_run_recovery(run.id)
    assert recovery["status"] == "interrupted"
    assert recovery["journal"][-1] == "tool done: file_write -> saved before tool returned"


def test_manual_compaction_targets_the_requested_session(version_client):
    client, _, (a, b), _, memories = version_client
    memories[a].maybe_compact = lambda **kwargs: 101
    memories[b].maybe_compact = lambda **kwargs: 202
    response = client.post('/api/version/compact', json={"session_id": b, "custom_instructions": "保留未完成步骤"})
    assert response.status_code == 200
    assert response.json()["tokens_saved"] == 202


def test_deleting_a_session_also_deletes_its_redo_path():
    path = get_settings().memory_db_path
    version = ConversationVersionManager("delete-redo", path)
    version.commit(question="first")
    version.commit(question="second")
    version.undo()
    ConversationVersionManager.delete_session(path, "delete-redo")
    recreated = ConversationVersionManager("delete-redo", path)
    assert recreated.status()["can_redo"] is False
    recreated.commit(question="recreated")
    assert recreated.status()["head"]["question"] == "recreated"
    assert recreated.status()["can_redo"] is False
    recreated._conn.close()
    version._conn.close()


def test_concurrent_recovery_store_initialization_shares_one_store(monkeypatch):
    from agentnexus.storage import timeline

    path = get_settings().memory_db_path
    create_store = timeline.TimelineStore
    initializing = Event()
    release = Event()
    second_requested = Event()

    def initialize(db_path):
        initializing.set()
        assert release.wait(5)
        return create_store(db_path)

    def read_second_store():
        second_requested.set()
        return timeline.get_timeline_store(path)

    monkeypatch.setattr(timeline, "TimelineStore", initialize)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(timeline.get_timeline_store, path)
        assert initializing.wait(5)
        second = pool.submit(read_second_store)
        assert second_requested.wait(5)
        try:
            second.result(timeout=0.1)
        except TimeoutError:
            pass
        finally:
            release.set()
        stores = [first.result(timeout=5), second.result(timeout=5)]
    assert stores[0] is stores[1]
    record = {"session_id": "concurrent", "run_id": "concurrent-run", "answer": "preserved"}
    stores[0].save_run_record(record)
    assert stores[1].get_run_record("concurrent-run") == record


def test_recovery_store_can_retry_after_initialization_failure(monkeypatch):
    from agentnexus.storage import timeline

    path = get_settings().memory_db_path
    connect = timeline.sqlite3.connect
    monkeypatch.setattr(timeline.sqlite3, "connect", MagicMock(side_effect=OSError("storage unavailable")))
    with pytest.raises(OSError):
        timeline.get_timeline_store(path).save_run_record({"session_id": "retry", "run_id": "retry-run"})
    monkeypatch.setattr(timeline.sqlite3, "connect", connect)
    record = {"session_id": "retry", "run_id": "retry-run", "answer": "preserved after storage recovered"}
    timeline.get_timeline_store(path).save_run_record(record)
    assert timeline.get_timeline_store(path).get_run_record("retry-run") == record
