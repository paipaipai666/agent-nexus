"""Integration tests for the HTTP API server."""

import pytest

pytest.importorskip("fastapi", reason="fastapi not installed")
from fastapi.testclient import TestClient


class _FakeChatService:
    class _Handle:
        id = "fake-session-id"
        skill = None
        profile = None

    def start_session(self, **kw):
        return self._Handle()


class _FakeServices:
    chat = _FakeChatService()


class _FakeRuntime:
    services = _FakeServices()

    def close(self):
        pass


@pytest.fixture
def server_app(temp_agentnexus_home):
    from agentnexus.server.app import create_app, set_runtime

    runtime = _FakeRuntime()
    set_runtime(runtime)
    app = create_app(runtime=runtime)
    yield app


@pytest.fixture
def client(server_app):
    return TestClient(server_app)


def test_health_endpoint(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_get_config(client):
    resp = client.get("/api/config")
    assert resp.status_code == 200
    data = resp.json()
    assert "llm_model_id" in data


def test_create_session(client):
    resp = client.post("/api/session", json={})
    assert resp.status_code == 200
    data = resp.json()
    assert "session_id" in data


def test_list_memories_empty(client):
    resp = client.get("/api/memory/list")
    assert resp.status_code == 200
    data = resp.json()
    assert "memories" in data


def test_stats_endpoint(client):
    resp = client.get("/api/stats")
    assert resp.status_code == 200


def test_list_documents_empty(client):
    resp = client.get("/api/kb/documents")
    assert resp.status_code == 200
    data = resp.json()
    assert "documents" in data
