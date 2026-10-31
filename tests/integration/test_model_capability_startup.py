"""Capability display stays local while execution discovers the wire protocol."""

import asyncio
import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
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
    state = SimpleNamespace(delay=0.0, stream_interval=0.0, stream_gap=0.0, requests=[], on_request=lambda payload: None)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def do_POST(self):
            try:
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            except (json.JSONDecodeError, OSError):
                return
            state.requests.append(payload)
            state.on_request(payload)
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
            parts = [f"data: {json.dumps(chunk)}\n\n".encode()]
            if state.stream_interval:
                parts.extend([b": keepalive\n\n"] * 30)
            parts.append(b"data: [DONE]\n\n")
            body = b"".join(parts)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                for index, part in enumerate(parts):
                    self.wfile.write(part)
                    self.wfile.flush()
                    if index == 0:
                        time.sleep(state.stream_gap)
                    time.sleep(state.stream_interval)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
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
async def test_tui_startup_does_not_probe_before_a_real_task(model_endpoint):
    from agentnexus.agents.re_act_agent import ReActAgent
    from agentnexus.tools.registry import ToolRegistry
    from agentnexus.tui.app import AgentNexusTUI

    app = AgentNexusTUI(agent=ReActAgent(model_endpoint.client, ToolRegistry()))
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause(0.2)
        assert app.screen.query_one("#chat-input").has_focus
        assert model_endpoint.requests == []


@pytest.mark.asyncio
async def test_tui_accepts_input_while_background_discovery_is_pending(model_endpoint):
    from textual.events import Key

    from agentnexus.agents.re_act_agent import ReActAgent
    from agentnexus.tools.registry import ToolRegistry
    from agentnexus.tui.app import AgentNexusTUI

    release = threading.Event()
    model_endpoint.on_request = lambda payload: release.wait(timeout=5)
    model_endpoint.client.start_capability_discovery()
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
        try:
            assert model_endpoint.client.capability_discovery_status == "probing"
            assert model_endpoint.client.capabilities.from_default_fallback is True
        finally:
            release.set()
        caps = await asyncio.to_thread(model_endpoint.client.ensure_capabilities)
        assert caps.supports_tool_calling is True


def test_capability_refresh_is_asynchronous_and_eventually_updates_http_snapshot(model_endpoint):
    from agentnexus.server.deps import get_runtime
    from agentnexus.server.routes.config import router

    model_endpoint.delay = 0.2
    app = FastAPI()
    app.include_router(router, prefix="/api/config")
    app.dependency_overrides[get_runtime] = lambda: SimpleNamespace(llm=model_endpoint.client, settings=get_settings())
    with TestClient(app) as client:
        started = time.perf_counter()
        response = client.post("/api/config/llm/capabilities/refresh")
        assert response.status_code == 200
        assert time.perf_counter() - started < 0.15
        assert response.json()["discovery_status"] == "probing"
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            snapshot = client.get("/api/config/llm/capabilities").json()
            if snapshot["discovery_status"] == "ready":
                break
            time.sleep(0.01)
    assert snapshot["source"] == "probe"
    assert snapshot["parallel_tool_calls"] is True


def test_first_task_preserves_native_tools_after_local_capability_read(model_endpoint):
    from agentnexus.agents.re_act_agent import ReActAgent
    from agentnexus.tools.registry import ToolRegistry

    assert model_endpoint.client.capabilities.from_default_fallback is True
    agent = ReActAgent(model_endpoint.client, ToolRegistry(), max_steps=2, output=lambda text: None)
    result = agent.run("Say done")
    assert result.answer == "done"
    assert "tool_calling" in model_endpoint.client.capabilities.unknown_features
    assert len(model_endpoint.requests) == 1
    assert all("合法的 JSON" not in message["content"] for message in model_endpoint.requests[-1]["messages"])


def test_successful_discovery_survives_a_new_process(model_endpoint):
    script = (
        "from agentnexus.core.llm import AgentLLM; "
        "import sys; "
        "c=AgentLLM(model='persisted-fixture',api_key=sys.argv[2],base_url=sys.argv[1]); "
        "print(c.ensure_capabilities().supports_parallel_tool_calls)"
    )
    for _ in range(2):
        result = subprocess.run([sys.executable, "-c", script, model_endpoint.url, "fixture-key"],
                                capture_output=True, text=True, timeout=15, check=True)
        assert result.stdout.strip() == "True"
    assert len(model_endpoint.requests) == 2
    assert "fixture-key" not in "".join(
        path.read_text(encoding="utf-8")
        for path in Path(os.environ["AGENTNEXUS_HOME"]).rglob("*.json")
    )


def test_same_model_shares_discovery_without_blocking_another_model(model_endpoint):
    barrier = threading.Barrier(2)
    simultaneous = []

    def meet_peer(payload):
        if payload["messages"][0]["content"] == "Call the noop tool.":
            try:
                barrier.wait(timeout=1)
                simultaneous.append(True)
            except threading.BrokenBarrierError:
                simultaneous.append(False)

    model_endpoint.on_request = meet_peer
    clients = [AgentLLM(model=model, api_key="fixture-key", base_url=model_endpoint.url)
               for model in ("model-a", "model-a", "model-b")]
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(client.ensure_capabilities) for client in clients]
        assert all(future.result(timeout=5).supports_tool_calling for future in futures)
    assert simultaneous == [True, True]
    assert len(model_endpoint.requests) == 4


def test_changing_credentials_does_not_reuse_discovery(model_endpoint):
    assert model_endpoint.client.ensure_capabilities().supports_tool_calling is True
    changed = AgentLLM(model="startup-fixture", api_key="different-fixture-key", base_url=model_endpoint.url)
    assert changed.ensure_capabilities().supports_tool_calling is True
    assert len(model_endpoint.requests) == 4


def test_model_switch_does_not_keep_a_snapshot_for_the_previous_model(model_endpoint, monkeypatch):
    from agentnexus.core.config import LLMProvider, ModelEntry, ModelOverride, Settings

    settings = Settings(llm_providers=[LLMProvider(
        name="fixture", base_url=model_endpoint.url, api_key="fixture-key",
        models=[
            ModelEntry(model_id="startup-fixture", override=ModelOverride(supports_tool_calling=False)),
            ModelEntry(model_id="new-model", override=ModelOverride(supports_tool_calling=True)),
        ],
    )])
    reading, release, switching, configured = (threading.Event() for _ in range(4))

    def read_settings():
        reading.set()
        assert release.wait(timeout=2)
        return settings

    def switch_model():
        switching.set()
        model_endpoint.client.configure(model="new-model", base_url=model_endpoint.url, api_key="fixture-key")
        configured.set()

    monkeypatch.setattr("agentnexus.core.capabilities.get_settings", read_settings)
    with ThreadPoolExecutor(max_workers=2) as pool:
        snapshot = pool.submit(model_endpoint.client.capability_snapshot)
        assert reading.wait(timeout=1)
        changed = pool.submit(switch_model)
        assert switching.wait(timeout=1)
        configured.wait(timeout=0.05)
        release.set()
        snapshot.result(timeout=2)
        changed.result(timeout=2)
    assert model_endpoint.client.capabilities.supports_tool_calling is True
    assert model_endpoint.requests == []


@pytest.mark.parametrize("corruption", ["expired", "invalid-json", "invalid-flags"])
def test_unusable_persistent_cache_is_rebuilt(model_endpoint, corruption):
    script = (
        "from agentnexus.core.llm import AgentLLM; import sys; "
        "c=AgentLLM(model='cache-fixture',api_key='fixture-key',base_url=sys.argv[1]); "
        "assert c.ensure_capabilities().supports_parallel_tool_calls"
    )
    subprocess.run([sys.executable, "-c", script, model_endpoint.url], check=True, timeout=15)
    path = next((Path(os.environ["AGENTNEXUS_HOME"]) / "capability-cache").glob("*.json"))
    data = json.loads(path.read_text(encoding="utf-8"))
    if corruption == "expired":
        data["expires_at"] = 0
    elif corruption == "invalid-flags":
        data["result"]["tool_calling"] = "false"
    path.write_text("incomplete" if corruption == "invalid-json" else json.dumps(data), encoding="utf-8")
    subprocess.run([sys.executable, "-c", script, model_endpoint.url], check=True, timeout=15)
    assert len(model_endpoint.requests) == (2 if corruption == "expired" else 4)


@pytest.mark.parametrize("mode", ["headers", "stream"])
@pytest.mark.parametrize("wire", ["openai", "anthropic"])
def test_discovery_has_a_short_total_deadline_and_persists_retry_backoff(model_endpoint, monkeypatch, mode, wire):
    import agentnexus.core.capability_discovery as discovery
    from agentnexus.core.providers.router import select_provider

    monkeypatch.setattr(discovery, "PROBE_BUDGET_SECONDS", 0.7)
    if wire == "anthropic":
        model_endpoint.client.configure(model="fixture", base_url=model_endpoint.url + "/anthropic.com/v1", api_key="fixture-key")
    if mode == "headers":
        model_endpoint.delay = 1.5
    else:
        model_endpoint.stream_interval = 0.1
    select_provider(model_endpoint.client.model, model_endpoint.client.base_url)
    started = time.monotonic()
    caps = model_endpoint.client.ensure_capabilities()
    elapsed = time.monotonic() - started
    assert elapsed < 1.2
    assert len(model_endpoint.requests) == 1
    assert caps.from_default_fallback is True
    assert model_endpoint.client.capability_discovery_status == "failed"
    cached = list((Path(os.environ["AGENTNEXUS_HOME"]) / "capability-cache").glob("*.json"))
    assert len(cached) == 1
    assert json.loads(cached[0].read_text(encoding="utf-8"))["result"] == {}
    request_count = len(model_endpoint.requests)
    model_endpoint.client.start_capability_discovery()
    assert model_endpoint.client.capability_discovery_status == "failed"
    assert len(model_endpoint.requests) == request_count
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline and any(t.name in {"capability-deadline", "openai-stream-pump"} for t in threading.enumerate()):
        time.sleep(0.01)
    assert not any(t.name in {"capability-deadline", "openai-stream-pump"} for t in threading.enumerate())


def test_discovery_budget_is_shared_by_both_requests(model_endpoint, monkeypatch):
    import agentnexus.core.capability_discovery as discovery
    from agentnexus.core.providers.router import select_provider

    monkeypatch.setattr(discovery, "PROBE_BUDGET_SECONDS", 1.5)
    model_endpoint.on_request = lambda payload: setattr(model_endpoint, "delay", 0.4 if len(model_endpoint.requests) == 1 else 1.2)
    select_provider(model_endpoint.client.model, model_endpoint.client.base_url)
    started = time.monotonic()
    caps = model_endpoint.client.ensure_capabilities()
    assert time.monotonic() - started < 1.9
    assert len(model_endpoint.requests) == 2
    assert caps.supports_tool_calling is True
    assert "parallel_tool_calls" in caps.unknown_features
    assert model_endpoint.client.capability_discovery_status == "failed"


def test_anthropic_stream_checks_deadline_when_transport_close_cannot_interrupt(model_endpoint, monkeypatch):
    from contextlib import nullcontext

    import agentnexus.core.capability_discovery as discovery
    import agentnexus.core.providers.anthropic_provider as provider

    monkeypatch.setattr(discovery, "PROBE_BUDGET_SECONDS", 0.7)
    monkeypatch.setattr(provider, "request_deadline", lambda deadline, close: nullcontext())
    model_endpoint.stream_interval = 0.1
    model_endpoint.client.configure(
        model="fixture", base_url=model_endpoint.url + "/anthropic.com/v1", api_key="fixture-key",
    )
    started = time.monotonic()
    caps = model_endpoint.client.ensure_capabilities()
    assert time.monotonic() - started < 1.2
    assert caps.from_default_fallback is True
    assert model_endpoint.client.capability_discovery_status == "failed"
    assert len(model_endpoint.requests) == 1
    cached = list((Path(os.environ["AGENTNEXUS_HOME"]) / "capability-cache").glob("*.json"))
    assert len(cached) == 1
    assert json.loads(cached[0].read_text(encoding="utf-8"))["result"] == {}


def test_anthropic_deadline_interrupts_a_pending_socket_read(model_endpoint, monkeypatch):
    import agentnexus.core.capability_discovery as discovery
    import agentnexus.core.providers.anthropic_provider as provider

    monkeypatch.setattr(discovery, "PROBE_BUDGET_SECONDS", 1.5)
    monkeypatch.setattr(provider.httpx.Client, "close", lambda self: None)
    model_endpoint.delay = 0.8
    model_endpoint.stream_gap = 3.0
    model_endpoint.client.configure(
        model="fixture", base_url=model_endpoint.url + "/anthropic.com/v1", api_key="fixture-key",
    )
    started = time.monotonic()
    caps = model_endpoint.client.ensure_capabilities()
    elapsed = time.monotonic() - started
    assert elapsed < 1.9, elapsed
    assert caps.from_default_fallback is True
    assert model_endpoint.client.capability_discovery_status == "failed"
