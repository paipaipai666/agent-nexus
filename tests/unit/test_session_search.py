from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentnexus.memory.versioned import ConversationVersionManager
from agentnexus.server.routes.chat import router


@pytest.fixture
def history(tmp_path):
    db = str(tmp_path / "history.sqlite")

    def add(session_id, content, workspace="/project", question="", answer=""):
        manager = ConversationVersionManager(session_id, db, workspace_path=workspace)
        if content:
            manager.append_message("user", "普通任务")
            manager.append_message("assistant", content)
        if question or answer:
            manager.commit(question=question, answer=answer)
        manager._conn.close()

    add("archived", "修复 PaymentTimeoutError，文件 src/payments.py")
    for index in range(55):
        add(f"recent-{index:02}", f"最近任务 {index}")
    return db, add


def test_search_finds_message_body_beyond_recent_page(history):
    db, _ = history
    sessions = ConversationVersionManager.find_recent_sessions(db, None, limit=5, query="PaymentTimeoutError")
    assert [session["session_id"] for session in sessions] == ["archived"]


def test_search_includes_checkpoint_answers(history):
    db, add = history
    add("checkpoint-only", "", question="检查构建", answer="BuildFailureDetail")
    sessions = ConversationVersionManager.find_recent_sessions(db, None, query="BuildFailureDetail")
    assert [session["session_id"] for session in sessions] == ["checkpoint-only"]


@pytest.mark.parametrize("query", ["100%", "file_name", "C:\\work", "' OR 1=1 --"])
def test_search_treats_sql_patterns_as_literal_text(history, query):
    db, add = history
    add("literal", f"实际内容 {query}")
    sessions = ConversationVersionManager.find_recent_sessions(db, None, query=query)
    assert [session["session_id"] for session in sessions] == ["literal"]


def test_search_keeps_workspace_scope_and_paginates_matches(history):
    db, add = history
    add("match-a", "needle", workspace="/one")
    add("match-b", "needle", workspace="/two")
    add("match-c", "needle", workspace="/one")
    first = ConversationVersionManager.find_recent_sessions(db, "/one", limit=1, query="needle")
    second = ConversationVersionManager.find_recent_sessions(db, "/one", limit=1, query="needle", offset=1)
    assert [session["session_id"] for session in first] == ["match-c"]
    assert [session["session_id"] for session in second] == ["match-a"]


@pytest.fixture
def client(history, monkeypatch):
    db, _ = history
    monkeypatch.setattr("agentnexus.core.config.get_settings", lambda: SimpleNamespace(memory_db_path=db))
    app = FastAPI()
    app.include_router(router, prefix="/api")
    with TestClient(app) as connection:
        yield connection


def test_recent_sessions_api_searches_before_limiting(client):
    response = client.get("/api/sessions/recent", params={"limit": 1, "query": "PaymentTimeoutError"})
    assert response.status_code == 200
    assert [session["session_id"] for session in response.json()["sessions"]] == ["archived"]
    assert response.json()["has_more"] is False


def test_recent_sessions_api_provides_next_page(client):
    first = client.get("/api/sessions/recent", params={"limit": 2}).json()
    second = client.get("/api/sessions/recent", params={"limit": 2, "offset": 2}).json()
    assert [session["session_id"] for session in first["sessions"]] == ["recent-54", "recent-53"]
    assert [session["session_id"] for session in second["sessions"]] == ["recent-52", "recent-51"]
    assert first["has_more"] is True


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 501}, {"offset": -1}, {"query": "x" * 201}])
def test_recent_sessions_api_rejects_invalid_pagination(client, params):
    assert client.get("/api/sessions/recent", params=params).status_code == 422
