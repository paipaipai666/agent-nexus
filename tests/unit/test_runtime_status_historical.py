"""Test /api/runtime/status returns stats for historical sessions from DB.

Bug: historical sessions (loaded from DB, no active agent instance) always
show 0 for input/output tokens and step_count because stats are never
persisted to the database.
"""

import sqlite3

import pytest

pytest.importorskip("fastapi", reason="fastapi not installed")
from fastapi.testclient import TestClient

# ── helpers ──────────────────────────────────────────────────────────

def _init_db(db_path: str):
    """Create the conversation_sessions table with stats columns."""
    from agentnexus.memory.versioned import (
        _MIGRATION_STATS_INPUT_SQL,
        _MIGRATION_STATS_OUTPUT_SQL,
        _MIGRATION_STATS_STEPS_SQL,
        SCHEMA,
    )
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    for sql in (_MIGRATION_STATS_INPUT_SQL, _MIGRATION_STATS_OUTPUT_SQL, _MIGRATION_STATS_STEPS_SQL):
        try:
            conn.execute(sql)
        except sqlite3.OperationalError:
            pass
    conn.commit()
    conn.close()


def _insert_session(db_path: str, session_id: str, workspace: str = "test_ws",
                    profile: str = "", preview: str = "",
                    input_tokens: int = 0, output_tokens: int = 0,
                    step_count: int = 0):
    """Insert a session row directly into the database."""
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO conversation_sessions "
        "(session_id, workspace_path, profile, preview) "
        "VALUES (?, ?, ?, ?)",
        (session_id, workspace, profile, preview),
    )
    # Try to update stats columns if they exist
    try:
        conn.execute(
            "UPDATE conversation_sessions SET "
            "total_input_tokens = ?, total_output_tokens = ?, step_count = ? "
            "WHERE session_id = ?",
            (input_tokens, output_tokens, step_count, session_id),
        )
    except sqlite3.OperationalError:
        pass  # Columns don't exist yet (before migration)
    conn.commit()
    conn.close()


# ── fakes ────────────────────────────────────────────────────────────

class _FakeAgent:
    model_id = "test-model"
    total_usage = {"input_tokens": 0, "output_tokens": 0}
    _step_count = 0


class _FakeMemoryManager:
    _ctx_max = 128000
    def estimate_stm_tokens(self):
        return 0


class _FakeChatService:
    def __init__(self):
        self._agents = {}
        self._memory_managers = {}
        self._sessions = {}


class _FakeServices:
    def __init__(self, chat=None):
        self.chat = chat
        self.skill = None


class _FakeSettings:
    llm_model_id = "test-model"
    max_context_tokens = 128000


class _FakeRuntime:
    def __init__(self, build_agent, memory_manager, services, settings=None, db_path=None):
        self.agent = build_agent
        self.memory_manager = memory_manager
        self.services = services
        self.settings = settings or _FakeSettings()
        self.mcp_manager = None
        self._db_path = db_path

    def close(self):
        pass


# ── fixtures ─────────────────────────────────────────────────────────

@pytest.fixture
def db_dir(tmp_path):
    return str(tmp_path / "test.db")


@pytest.fixture
def server_app(temp_agentnexus_home, db_dir):
    from agentnexus.server.app import create_app, set_runtime

    _init_db(db_dir)

    # Build-time agent: zero stats (always)
    build_agent = _FakeAgent()
    build_mm = _FakeMemoryManager()

    chat = _FakeChatService()

    # Historical session: exists in DB with stats, NO agent in chat._agents
    _insert_session(db_dir, "hist-1", input_tokens=3000, output_tokens=1500, step_count=5)
    chat._sessions["hist-1"] = type("Handle", (), {"id": "hist-1", "skill": None, "profile": None})()

    # Historical session with ZERO stats (new session, never ran)
    _insert_session(db_dir, "hist-new", input_tokens=0, output_tokens=0, step_count=0)
    chat._sessions["hist-new"] = type("Handle", (), {"id": "hist-new", "skill": None, "profile": None})()

    # Active session: has agent in chat._agents (in-memory stats)
    active_agent = _FakeAgent()
    active_agent.total_usage = {"input_tokens": 5000, "output_tokens": 2500}
    active_agent._step_count = 8
    chat._agents["active-1"] = active_agent
    chat._memory_managers["active-1"] = _FakeMemoryManager()
    chat._sessions["active-1"] = type("Handle", (), {"id": "active-1", "skill": None, "profile": None})()

    services = _FakeServices(chat=chat)
    runtime = _FakeRuntime(build_agent, build_mm, services, db_path=db_dir)
    set_runtime(runtime)
    app = create_app(runtime=runtime)
    yield app


@pytest.fixture
def client(server_app):
    return TestClient(server_app)


# ── RED: these should FAIL before the fix ────────────────────────────

def test_historical_session_returns_persisted_token_usage(client):
    """Historical session should return persisted input/output tokens from DB."""
    resp = client.get("/api/runtime/status", params={"session_id": "hist-1"})
    assert resp.status_code == 200
    data = resp.json()

    usage = data["total_usage"]
    assert usage["input_tokens"] == 3000, f"Expected input_tokens=3000, got {usage['input_tokens']}"
    assert usage["output_tokens"] == 1500, f"Expected output_tokens=1500, got {usage['output_tokens']}"


def test_historical_session_returns_persisted_step_count(client):
    """Historical session should return persisted step_count from DB."""
    resp = client.get("/api/runtime/status", params={"session_id": "hist-1"})
    assert resp.status_code == 200
    data = resp.json()

    assert data["step_count"] == 5, f"Expected step_count=5, got {data['step_count']}"


def test_historical_session_new_returns_zeros(client):
    """Historical session that never ran should return zeros."""
    resp = client.get("/api/runtime/status", params={"session_id": "hist-new"})
    assert resp.status_code == 200
    data = resp.json()

    assert data["total_usage"]["input_tokens"] == 0
    assert data["total_usage"]["output_tokens"] == 0
    assert data["step_count"] == 0


def test_active_session_still_uses_in_memory_stats(client):
    """Active session with agent in chat._agents should use in-memory stats."""
    resp = client.get("/api/runtime/status", params={"session_id": "active-1"})
    assert resp.status_code == 200
    data = resp.json()

    usage = data["total_usage"]
    assert usage["input_tokens"] == 5000
    assert usage["output_tokens"] == 2500
    assert data["step_count"] == 8


def test_unknown_session_returns_zeros(client):
    """Unknown session_id should return zeros gracefully."""
    resp = client.get("/api/runtime/status", params={"session_id": "nonexistent"})
    assert resp.status_code == 200
    data = resp.json()

    assert data["total_usage"]["input_tokens"] == 0
    assert data["total_usage"]["output_tokens"] == 0
    assert data["step_count"] == 0


def test_no_session_id_returns_zeros(client):
    """Without session_id, should return build-time zeros."""
    resp = client.get("/api/runtime/status")
    assert resp.status_code == 200
    data = resp.json()

    assert data["total_usage"]["input_tokens"] == 0
    assert data["total_usage"]["output_tokens"] == 0
    assert data["step_count"] == 0


def test_stats_persisted_via_version_manager_then_read(client, db_dir):
    """Full flow: persist stats via ConversationVersionManager, then read via endpoint."""
    from agentnexus.memory.versioned import ConversationVersionManager

    vm = ConversationVersionManager("flow-test", db_dir, workspace_path="test_ws")
    vm.update_session_stats(input_tokens=7777, output_tokens=4444, step_count=12)

    resp = client.get("/api/runtime/status", params={"session_id": "flow-test"})
    assert resp.status_code == 200
    data = resp.json()

    assert data["total_usage"]["input_tokens"] == 7777
    assert data["total_usage"]["output_tokens"] == 4444
    assert data["step_count"] == 12


def test_pre_existing_db_without_stats_columns(tmp_path):
    """Simulate a DB created before the stats migration — columns added on read."""
    from agentnexus.memory.versioned import SCHEMA, ConversationVersionManager

    db_path = str(tmp_path / "old.db")
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO conversation_sessions (session_id, workspace_path) VALUES (?, ?)",
                 ("old-session", "ws"))
    conn.commit()
    conn.close()

    # get_session_stats should run migration and read zeros (no stats written yet)
    result = ConversationVersionManager.get_session_stats(db_path, "old-session")
    assert result == {"input_tokens": 0, "output_tokens": 0, "step_count": 0}

    # Now persist stats — migration should have already run in get_session_stats
    vm = ConversationVersionManager("old-session", db_path, workspace_path="ws")
    vm.update_session_stats(input_tokens=100, output_tokens=50, step_count=2)

    result = ConversationVersionManager.get_session_stats(db_path, "old-session")
    assert result == {"input_tokens": 100, "output_tokens": 50, "step_count": 2}
