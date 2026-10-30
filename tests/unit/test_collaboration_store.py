import pytest

from agentnexus.storage.timeline import TimelineStore


def message(**fields):
    return {"session_id": "s", "run_id": "p", "request_id": "request", "sender": "child", "recipient": "parent", "subagent_id": "child", "child_run_id": "attempt", "kind": "question", "content": "Which format?", "question_id": "", **fields}


def test_message_and_receipts_survive_reopen_without_duplicate_submission(tmp_path):
    path = str(tmp_path / "timeline.db")
    store = TimelineStore(path)
    first = store.create_collaboration_message(message())
    assert first["delivery"] == "queued"
    assert store.create_collaboration_message(message())["id"] == first["id"]
    with pytest.raises(ValueError):
        store.create_collaboration_message(message(content="different"))
    store.apply_collaboration_messages([first["id"]], 3)
    reopened = TimelineStore(path)
    saved = reopened.list_collaboration_messages("s", "child")[0]
    assert (saved["delivery"], saved["step_id"], saved["question_state"]) == ("applied", 3, "open")


def test_storage_failure_is_explicit():
    store = TimelineStore("")
    with pytest.raises(OSError):
        store.create_collaboration_message(message())


def test_different_senders_can_use_the_same_request_id(tmp_path):
    store = TimelineStore(str(tmp_path / "timeline.db"))
    first = store.create_collaboration_message(message())
    second = store.create_collaboration_message(message(sender="other", subagent_id="other", child_run_id="other_attempt"))
    assert first["id"] != second["id"]


def test_closing_attempt_rejects_unread_messages_and_questions(tmp_path):
    store = TimelineStore(str(tmp_path / "timeline.db"))
    first = store.create_collaboration_message(message())
    store.close_collaboration("s", "p", "cancelled", child_run_id="attempt")
    saved = store.list_collaboration_messages("s")[0]
    assert saved["id"] == first["id"]
    assert (saved["delivery"], saved["question_state"], saved["reason"]) == ("queued", "closed", "cancelled")


def test_restart_appends_interrupted_child_state_and_closes_pending_messages(tmp_path):
    store = TimelineStore(str(tmp_path / "timeline.db"))
    store.record_event("s", "p", 1, "subagent_event", {"subagent_id": "child", "kind": "started", "name": "Reader", "role": "explorer", "task": "Read", "status": "thinking"})
    store.record_event("s", "p", 2, "subagent_event", {"subagent_id": "child", "kind": "waiting", "status": "waiting", "child_run_id": "attempt"})
    store.create_collaboration_message(message())
    store.recover_collaboration()
    rows = store.list_events("s", normalize=False)
    assert [row["payload"]["kind"] for row in rows] == ["started", "waiting", "finished"]
    assert rows[-1]["payload"]["status"] == "interrupted"
    saved = store.list_collaboration_messages("s")[0]
    assert (saved["delivery"], saved["question_state"]) == ("rejected", "closed")
    store.recover_collaboration()
    assert len(store.list_events("s")) == 3
