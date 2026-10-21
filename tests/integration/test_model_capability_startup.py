"""Capability display stays local while execution discovers the wire protocol."""

import asyncio
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentnexus.core.config import get_settings
from agentnexus.core.llm import AgentLLM
from agentnexus.core.providers.router import close_providers


@pytest.fixture
def model_endpoint(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    state = SimpleNamespace(delay=0.0, requests=[])

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state.requests.append(payload)
            time.sleep(state.delay)
            prompt = payload["messages"][-1]["content"]
            delta = {"content": "done"}
            if "noop" in prompt:
                count = 2 if "twice" in prompt else 1
                delta = {"tool_calls": [
                    {"index": index, "id": f"call_{index}", "type": "function",
                     "function": {"name": "noop", "arguments": "{}"}}
                    for index in range(count)
                ]}
            chunk = {"id": "fixture", "object": "chat.completion.chunk", "created": 0,
                     "model": payload["model"], "choices": [{"index": 0, "delta": delta, "finish_reason": "stop"}]}
            body = f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n".encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
    thread.start()
    state.url = f"http://127.0.0.1:{server.server_port}/v1"
    state.client = AgentLLM(model="startup-fixture", api_key="fixture-key", base_url=state.url, timeout=2)
    try:
        yield state
    finally:
        close_providers()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_capability_http_read_returns_unknown_without_contacting_model(model_endpoint):
    from agentnexus.server.deps import get_runtime
    from agentnexus.server.routes.config import router

    model_endpoint.delay = 0.3
    app = FastAPI()
    app.include_router(router, prefix="/api/config")
    app.dependency_overrides[get_runtime] = lambda: SimpleNamespace(llm=model_endpoint.client, settings=get_settings())
    with TestClient(app) as client:
        started = time.perf_counter()
        response = client.get("/api/config/llm/capabilities")
        elapsed = time.perf_counter() - started
    assert response.status_code == 200
    assert response.json()["source"] == "unknown"
    assert elapsed < 0.25
    assert model_endpoint.requests == []


@pytest.mark.asyncio
async def test_tui_accepts_input_without_contacting_model(model_endpoint):
    from textual.events import Key

    from agentnexus.agents.re_act_agent import ReActAgent
    from agentnexus.tools.registry import ToolRegistry
    from agentnexus.tui.app import AgentNexusTUI

    model_endpoint.delay = 0.3
    app = AgentNexusTUI(agent=ReActAgent(model_endpoint.client, ToolRegistry()))
    async with app.run_test(size=(100, 30)):
        async with asyncio.timeout(3):
            while not app.screen.query("#chat-input") or not app.screen.query_one("#chat-input").has_focus:
                await asyncio.sleep(0.005)
            composer = app.screen.query_one("#chat-input")
            app._driver.send_message(Key("x", "x"))
            while composer.text != "x":
                await asyncio.sleep(0.005)
        assert composer.text == "x"
        assert model_endpoint.requests == []


def test_first_task_preserves_native_tools_after_local_capability_read(model_endpoint):
    from agentnexus.agents.re_act_agent import ReActAgent
    from agentnexus.tools.registry import ToolRegistry

    assert model_endpoint.client.capabilities.from_default_fallback is True
    agent = ReActAgent(model_endpoint.client, ToolRegistry(), max_steps=2, output=lambda text: None)
    result = agent.run("Say done")
    assert result.answer == "done"
    assert model_endpoint.client.capabilities.supports_tool_calling is True
    assert len(model_endpoint.requests) == 3
    assert all("合法的 JSON" not in message["content"] for message in model_endpoint.requests[-1]["messages"])
