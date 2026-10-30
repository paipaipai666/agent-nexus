"""Application services survive removal of the default agent implementation package."""

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentnexus.server.deps import get_runtime
from agentnexus.server.routes.runtime import router


def test_runtime_status_reads_public_metadata_from_an_independent_engine():
    class Usage:
        def to_dict(self, *, capacity):
            return {"used_tokens": 12, "free_tokens": capacity - 12, "estimated": True}

    engine = SimpleNamespace(model_id="selected/model", step_count=7,
                             total_usage={"input_tokens": 12, "output_tokens": 3}, context_usage=Usage())
    runtime = SimpleNamespace(agent=engine, memory_manager=None, skill=None,
                              settings=SimpleNamespace(max_context_tokens=100),
                              chat=SimpleNamespace(_agents={"s": engine}, _memory_managers={}))
    app = FastAPI()
    app.include_router(router, prefix="/api/runtime")
    app.dependency_overrides[get_runtime] = lambda: runtime
    with TestClient(app) as client:
        response = client.get("/api/runtime/status")
    assert response.status_code == 200
    assert response.json()["step_count"] == 7
    assert response.json()["context_usage"] == {"used_tokens": 12, "free_tokens": 88, "estimated": True}


def test_custom_chat_and_server_start_without_the_default_agent_package(tmp_path):
    program = '''
import importlib.abc
import sys
from dataclasses import replace

class MissingAgent(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "agentnexus.agents" or fullname.startswith("agentnexus.agents."):
            raise ModuleNotFoundError(fullname, name=fullname)

sys.meta_path.insert(0, MissingAgent())
from fastapi.testclient import TestClient
from agentnexus.app.runtime import AppRuntime
from agentnexus.core.modules import ModuleFactories
from agentnexus.server.app import create_app

class Engine:
    def run(self, question, *, memory_manager=None):
        return "custom conversation works"

factories = replace(ModuleFactories.disabled(), agent=lambda context: Engine())
runtime = AppRuntime.build(module_factories=factories)
with TestClient(create_app(runtime)) as client:
    session = runtime.chat.start_session()
    run = runtime.chat.send_message(session.id, "hello")
    list(runtime.chat.stream_events(run.id))
    assert runtime.chat.get_run_recovery(run.id)["answer"] == "custom conversation works"
    assert runtime.chat.get_run_recovery(run.id)["persisted"] is True
    assert client.get("/api/sessions").status_code == 200
    assert client.get("/api/runtime/status").status_code == 200
print("custom chat and server completed without the default agent package")
'''
    environment = dict(os.environ, AGENTNEXUS_HOME=str(tmp_path))
    result = subprocess.run([sys.executable, "-X", "utf8", "-c", program], cwd=Path(__file__).resolve().parents[2],
                            env=environment, capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "custom chat and server completed" in result.stdout
