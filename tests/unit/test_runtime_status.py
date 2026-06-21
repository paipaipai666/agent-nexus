"""Test /api/runtime/status endpoint returns per-session stats.

Bug: endpoint reads from build-time agent (always zero) instead of
per-session agent that actually accumulates token usage and step counts.
"""

import pytest

pytest.importorskip("fastapi", reason="fastapi not installed")
from fastapi.testclient import TestClient


# ── fakes ────────────────────────────────────────────────────────────

class _FakeAgent:
    """Simulates a per-session agent that has run queries and accumulated stats."""
    model_id = "test-model"
    total_usage = {"input_tokens": 0, "output_tokens": 0}
    _step_count = 0

    def __init__(self, input_tokens=0, output_tokens=0, step_count=0):
        self.total_usage = {"input_tokens": input_tokens, "output_tokens": output_tokens}
        self._step_count = step_count


class _FakeMemoryManager:
    """Simulates a per-session memory manager with STM tokens."""
    _ctx_max = 128000
    _stm_tokens = 0

    def __init__(self, stm_tokens=0):
        self._stm_tokens = stm_tokens

    def estimate_stm_tokens(self):
        return self._stm_tokens


class _FakeChatService:
    """Simulates ChatService with per-session agents and memory managers."""
    def __init__(self):
        self._agents = {}
        self._memory_managers = {}

    def add_session(self, session_id, agent, memory):
        self._agents[session_id] = agent
        self._memory_managers[session_id] = memory


class _FakeServices:
    def __init__(self, chat=None):
        self.chat = chat
        self.skill = None


class _FakeSettings:
    llm_model_id = "test-model"
    max_context_tokens = 128000


class _FakeRuntime:
    """Simulates AppRuntime with build-time agent (zero stats) and per-session agents."""
    def __init__(self, build_agent, memory_manager, services):
        self.agent = build_agent  # build-time agent, never runs queries
        self.memory_manager = memory_manager  # build-time memory manager, empty STM
        self.services = services
        self.settings = _FakeSettings()
        self.mcp_manager = None

    def close(self):
        pass


# ── fixtures ─────────────────────────────────────────────────────────

@pytest.fixture
def server_app(temp_agentnexus_home):
    from agentnexus.server.app import create_app, set_runtime

    # Build-time agent: zero stats (this is the bug — endpoint reads from here)
    build_agent = _FakeAgent(input_tokens=0, output_tokens=0, step_count=0)
    build_mm = _FakeMemoryManager(stm_tokens=0)

    # Per-session agents: have actual stats (endpoint should read from here)
    chat = _FakeChatService()
    chat.add_session(
        "session-1",
        agent=_FakeAgent(input_tokens=1500, output_tokens=800, step_count=3),
        memory=_FakeMemoryManager(stm_tokens=4200),
    )
    chat.add_session(
        "session-2",
        agent=_FakeAgent(input_tokens=5000, output_tokens=2000, step_count=7),
        memory=_FakeMemoryManager(stm_tokens=9800),
    )

    services = _FakeServices(chat=chat)
    runtime = _FakeRuntime(build_agent, build_mm, services)
    set_runtime(runtime)
    app = create_app(runtime=runtime)
    yield app


@pytest.fixture
def client(server_app):
    return TestClient(server_app)


# ── RED: these should FAIL before the fix ────────────────────────────

def test_runtime_status_returns_per_session_token_usage(client):
    """Bug: input_tokens and output_tokens are always 0."""
    resp = client.get("/api/runtime/status", params={"session_id": "session-1"})
    assert resp.status_code == 200
    data = resp.json()

    usage = data["total_usage"]
    assert usage["input_tokens"] == 1500, f"Expected input_tokens=1500, got {usage['input_tokens']}"
    assert usage["output_tokens"] == 800, f"Expected output_tokens=800, got {usage['output_tokens']}"


def test_runtime_status_returns_per_session_step_count(client):
    """Bug: step_count is always 0."""
    resp = client.get("/api/runtime/status", params={"session_id": "session-1"})
    assert resp.status_code == 200
    data = resp.json()

    assert data["step_count"] == 3, f"Expected step_count=3, got {data['step_count']}"


def test_runtime_status_returns_per_session_stm_tokens(client):
    """Bug: stm_tokens is always 0."""
    resp = client.get("/api/runtime/status", params={"session_id": "session-1"})
    assert resp.status_code == 200
    data = resp.json()

    assert data["stm_tokens"] == 4200, f"Expected stm_tokens=4200, got {data['stm_tokens']}"


def test_runtime_status_different_sessions_return_different_stats(client):
    """Each session should return its own stats, not shared zeros."""
    resp1 = client.get("/api/runtime/status", params={"session_id": "session-1"})
    resp2 = client.get("/api/runtime/status", params={"session_id": "session-2"})
    assert resp1.status_code == 200
    assert resp2.status_code == 200

    d1 = resp1.json()
    d2 = resp2.json()

    assert d1["total_usage"]["input_tokens"] == 1500
    assert d2["total_usage"]["input_tokens"] == 5000
    assert d1["step_count"] == 3
    assert d2["step_count"] == 7
    assert d1["stm_tokens"] == 4200
    assert d2["stm_tokens"] == 9800


def test_runtime_status_without_session_id_falls_back_to_build_agent(client):
    """Without session_id, should return build-time stats (zeros) gracefully."""
    resp = client.get("/api/runtime/status")
    assert resp.status_code == 200
    data = resp.json()

    # Should return zeros for unknown session (build-time fallback)
    assert data["total_usage"]["input_tokens"] == 0
    assert data["total_usage"]["output_tokens"] == 0
    assert data["step_count"] == 0


def test_runtime_status_unknown_session_returns_zeros(client):
    """Unknown session_id should return zeros gracefully."""
    resp = client.get("/api/runtime/status", params={"session_id": "nonexistent"})
    assert resp.status_code == 200
    data = resp.json()

    assert data["total_usage"]["input_tokens"] == 0
    assert data["total_usage"]["output_tokens"] == 0
    assert data["step_count"] == 0


def test_runtime_status_model_id_from_settings(client):
    """model_id should come from settings when agent has none."""
    resp = client.get("/api/runtime/status", params={"session_id": "session-1"})
    assert resp.status_code == 200
    data = resp.json()

    assert data["model_id"] == "test-model"


def test_runtime_status_ctx_max(client):
    """ctx_max should always come from config (same for all sessions)."""
    resp = client.get("/api/runtime/status", params={"session_id": "session-1"})
    assert resp.status_code == 200
    data = resp.json()

    assert data["ctx_max"] == 128000
