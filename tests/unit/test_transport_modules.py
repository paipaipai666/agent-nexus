"""Transport entry points preserve the runtime's selected module providers."""

import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from agentnexus.core.modules import ModuleFactories, ModuleHost
from agentnexus.server.app import create_app


def runtime_for(host):
    class Runtime:
        modules = host
        settings = host.settings
        llm = None
        memory_manager = None
        mcp_manager = None
        chat = SimpleNamespace(_sessions={}, cancel_all_runs=lambda **kwargs: None)
        closed = False

        def close(self):
            self.closed = True
            self.modules.close()

    return Runtime()


def test_disabled_modules_return_unavailability_without_breaking_sessions(monkeypatch):
    monkeypatch.setattr("agentnexus.server.app._mark_stale_ingestion_runs", lambda: None)
    host = ModuleHost(factories=ModuleFactories.disabled())
    with TestClient(create_app(runtime_for(host)), raise_server_exceptions=False) as client:
        assert client.get("/api/sessions").json() == {"sessions": [], "count": 0}
        for path, module in [
            ("/api/wiki/stats", "wiki"),
            ("/api/memory/long", "long_term"),
            ("/api/eval/datasets", "evaluation"),
        ]:
            response = client.get(path)
            assert response.status_code == 503, (path, response.text)
            assert response.json()["error"]["code"] == "module_unavailable"
            assert module in response.json()["error"]["message"]


def selected_host(label):
    class Wiki:
        def get_stats(self, namespace):
            return {"owner": label, "namespace": namespace}

    class Memory:
        def list_recent(self, limit):
            return [{"content": label}]

    class Evaluation:
        def list_datasets(self):
            return [{"name": label}]

        def list_reports(self):
            return [{"report": label}]

    return ModuleHost(factories=replace(
        ModuleFactories.disabled(), wiki=lambda context: Wiki(),
        long_term=lambda context: Memory(), evaluation=lambda context: Evaluation(),
    ))


def test_simultaneous_server_apps_keep_their_selected_providers_and_lifetimes(monkeypatch):
    monkeypatch.setattr("agentnexus.server.app._mark_stale_ingestion_runs", lambda: None)
    first = runtime_for(selected_host("first"))
    second = runtime_for(selected_host("second"))
    with TestClient(create_app(first)) as first_client:
        with TestClient(create_app(second)) as second_client:
            for client, label in [(first_client, "first"), (second_client, "second"), (first_client, "first")]:
                assert client.get("/api/wiki/stats").json() == {"owner": label, "namespace": "default"}
                assert client.get("/api/memory/long").json()["memories"] == [{"content": label}]
                assert client.get("/api/eval/datasets").json() == {"datasets": [{"name": label}]}
                assert client.get("/api/eval/reports").json() == {"reports": [{"report": label}]}
        assert second.closed is True
        assert first.closed is False
        assert first_client.get("/api/wiki/stats").json()["owner"] == "first"
    assert first.closed is True


def test_disabled_modules_do_not_require_llm_or_create_trace_directory(tmp_path):
    from agentnexus.server.health_checks import check_llm_health, check_traces_dir

    host = ModuleHost(factories=ModuleFactories.disabled())
    runtime = runtime_for(host)
    runtime.settings = SimpleNamespace(traces_dir=str(tmp_path / "must-not-exist"))
    assert check_llm_health(runtime) == {"status": "ok", "detail": "Agent module disabled"}
    assert check_traces_dir(runtime) == {"status": "ok", "detail": "Observability module disabled"}
    assert not (tmp_path / "must-not-exist").exists()


def test_cli_reports_disabled_module_without_an_exception_trace():
    from agentnexus.cli import app

    host = ModuleHost(factories=ModuleFactories.disabled())
    with host.activate():
        result = CliRunner().invoke(app, ["eval", "benchmark", "list"])
    assert result.exit_code == 1
    assert "Module evaluation is disabled" in result.output
    assert "Traceback" not in result.output


def test_cli_health_does_not_construct_llm_for_disabled_agent(monkeypatch):
    from agentnexus.cli import app

    def unexpected_llm():
        raise AssertionError("Disabled agent reconstructed an LLM")

    monkeypatch.setattr("agentnexus.core.llm.AgentLLM", unexpected_llm)
    host = ModuleHost(factories=ModuleFactories.disabled())
    with host.activate():
        result = CliRunner().invoke(app, ["health"])
    assert result.exit_code == 0, result.output
    assert "Agent module disabled" in result.output


def test_custom_observation_health_avoids_default_trace_storage(tmp_path):
    from agentnexus.server.health_checks import check_traces_dir

    settings = SimpleNamespace(traces_dir=str(tmp_path / "unused-default"))
    host = ModuleHost(settings, factories=replace(ModuleFactories.disabled(), observability=lambda context: object()))
    result = check_traces_dir(runtime_for(host))
    assert result == {"status": "ok", "detail": "Observability provider ready"}
    assert not (tmp_path / "unused-default").exists()


def test_tui_launcher_preserves_selected_modules(monkeypatch):
    from agentnexus.cli import app

    captured = []
    owner = ModuleHost(factories=ModuleFactories.disabled())

    class Terminal:
        def __init__(self, **components):
            self.runtime = components["runtime"]

        def run(self):
            captured.append(self.runtime.modules)
            assert self.runtime.agent is None

    monkeypatch.setattr("agentnexus.tui.app.AgentNexusTUI", Terminal)
    with owner.activate():
        result = CliRunner().invoke(app, ["tui"])
    assert result.exit_code == 0, result.output
    assert captured == [owner]


def test_serve_launcher_preserves_selected_modules(monkeypatch):
    from agentnexus.cli import app

    owner = selected_host("cli-server")
    captured = []

    def run_server(application, **options):
        with TestClient(application) as client:
            captured.append(application.state.runtime.modules)
            assert client.get("/api/wiki/stats").json()["owner"] == "cli-server"

    monkeypatch.setattr("uvicorn.run", run_server)
    with owner.activate():
        result = CliRunner().invoke(app, ["serve", "--no-auth"])
    assert result.exit_code == 0, result.output
    assert captured == [owner]


def test_disabled_agent_websocket_reports_module_and_keeps_connection_usable(monkeypatch):
    from agentnexus.app.runtime import AppRuntime

    monkeypatch.setattr("agentnexus.server.auth.get_token", lambda: None)
    runtime = AppRuntime.build(module_factories=ModuleFactories.disabled())
    with TestClient(create_app(runtime)) as client:
        session_id = client.post("/api/session", json={}).json()["session_id"]
        with client.websocket_connect(f"/api/ws/agent/{session_id}") as websocket:
            websocket.send_json({"type": "send_message", "content": "hello", "client_message_id": "disabled-agent"})
            response = websocket.receive_json()
            assert response == {"type": "submission_rejected", "client_message_id": "disabled-agent",
                                "code": "module_unavailable", "message": "Module agent is disabled"}
            websocket.send_json({"type": "ping"})
            assert websocket.receive_json() == {"type": "pong"}
        assert runtime.chat.get_submission_receipt(session_id, "disabled-agent") is None


def test_websocket_execution_uses_each_apps_selected_engine_and_provider(monkeypatch):
    from agentnexus.app.runtime import AppRuntime
    from agentnexus.core.modules import get_module_host

    monkeypatch.setattr("agentnexus.server.auth.get_token", lambda: None)

    class Engine:
        def run(self, question, **options):
            return get_module_host().require("wiki").get_stats("default")["owner"]

    def build(label):
        host = selected_host(label)
        factories = replace(host.factories, agent=lambda context: Engine())
        return AppRuntime.build(module_factories=factories)

    first = build("first-websocket")
    second = build("second-websocket")
    with TestClient(create_app(first)) as first_client, TestClient(create_app(second)) as second_client:
        for client, expected in [(first_client, "first-websocket"), (second_client, "second-websocket")]:
            session_id = client.post("/api/session", json={}).json()["session_id"]
            with client.websocket_connect(f"/api/ws/agent/{session_id}") as websocket:
                websocket.send_json({"type": "send_message", "content": "which owner"})
                answer = None
                while True:
                    message = websocket.receive_json()
                    if message["type"] == "answer":
                        answer = message["content"]
                    if message["type"] == "done":
                        break
                assert answer == expected


@pytest.mark.parametrize("disabled", [False, True])
def test_removed_defaults_leave_server_and_cli_entry_points_operational(disabled, tmp_path):
    script = '''
import importlib.abc
import sys
from pathlib import Path

blocked = ("agentnexus.agents.re_act_agent", "agentnexus.memory.short_term", "agentnexus.memory.long_term", "agentnexus.wiki", "agentnexus.eval", "agentnexus.evaluation", "agentnexus.observability")

class MissingDefaults(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == name or fullname.startswith(name + ".") for name in blocked):
            raise ModuleNotFoundError("Removed implementation: " + fullname, name=fullname)

sys.meta_path.insert(0, MissingDefaults())
from agentnexus.app.runtime import AppRuntime
from agentnexus.core.modules import ModuleFactories
from agentnexus.server.app import create_app
from fastapi.testclient import TestClient
from typer.testing import CliRunner
from agentnexus.cli import app

runtime = AppRuntime.build(module_factories=ModuleFactories.disabled() if DISABLED else None)
with TestClient(create_app(runtime), raise_server_exceptions=False) as client:
    session = client.post("/api/session", json={})
    assert session.status_code == 200, session.text
    assert client.get("/api/sessions").status_code == 200
    response = client.post("/api/chat", json={"session_id": session.json()["session_id"], "content": "hello"})
    assert response.status_code == 503, response.text
    assert response.json()["error"]["code"] == "module_unavailable"
    for endpoint in ("/api/wiki/stats", "/api/memory/long", "/api/eval/datasets"):
        response = client.get(endpoint)
        assert response.status_code == 503, (endpoint, response.text)
result = CliRunner().invoke(app, ["version"])
assert result.exit_code == 0, result.output
assert "AgentNexus" in result.output
assert not any(any(name == package or name.startswith(package + ".") for package in blocked) for name in sys.modules)
print("server-and-cli-ok")
'''.replace("DISABLED", repr(disabled))
    environment = {**os.environ, "AGENTNEXUS_HOME": str(tmp_path)}
    result = subprocess.run([sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[2],
                            env=environment, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "server-and-cli-ok" in result.stdout
