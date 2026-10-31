"""Unknown protocols are attempted from the top and confirmed on real requests."""

import httpx
import pytest
from openai import BadRequestError, RateLimitError

from agentnexus.agents.decisions import select_strategy
from agentnexus.agents.re_act_agent import ReActAgent
from agentnexus.agents.react_types import CallingStrategy
from agentnexus.core.capabilities import SessionCapabilityTracker, detect_capabilities
from agentnexus.core.config import get_settings
from agentnexus.core.llm import AgentLLM
from agentnexus.core.providers.base import StreamResult
from agentnexus.tools.registry import ToolMeta, ToolRegistry


def test_unknown_model_starts_with_native_protocol():
    caps = detect_capabilities("unknown-fixture", "https://example.invalid")
    assert select_strategy(SessionCapabilityTracker(), caps) is CallingStrategy.NATIVE_TOOLS


def test_explicit_tool_rejection_attempts_unknown_json_mode_next():
    settings = get_settings()
    settings.model_tool_calling = False
    caps = detect_capabilities("unknown-fixture", "https://example.invalid")
    assert select_strategy(SessionCapabilityTracker(), caps) is CallingStrategy.JSON_MODE


def test_two_explicit_protocol_rejections_use_prompt_json():
    settings = get_settings()
    settings.model_tool_calling = False
    settings.model_json_mode = False
    caps = detect_capabilities("unknown-fixture", "https://example.invalid")
    assert select_strategy(SessionCapabilityTracker(), caps) is CallingStrategy.PROMPT_JSON


def test_first_task_sends_tools_without_extra_probe_requests(monkeypatch):
    requests = []

    class Provider:
        def stream_chat(self, **payload):
            requests.append(payload)
            return StreamResult(text="actual task completed")

    monkeypatch.setattr("agentnexus.core.llm.select_provider", lambda *_: Provider())
    client = AgentLLM(model="task-fixture", api_key="fixture", base_url="https://example.invalid")
    registry = ToolRegistry()
    registry.register(ToolMeta(name="echo", description="Echo input", param_schema={"type": "object"}), lambda: "echo")
    result = ReActAgent(client, registry, max_steps=2, output=lambda _: None).run("Say hello")
    assert result.answer == "actual task completed"
    assert len(requests) == 1
    assert requests[0]["tools"][0]["function"]["name"] == "echo"
    assert all("Call the noop tool" not in message["content"] for message in requests[0]["messages"])


@pytest.mark.parametrize("limited", [False, True])
def test_temporary_failure_does_not_disable_native_tools(monkeypatch, limited):
    class Provider:
        def stream_chat(self, **payload):
            if limited:
                raise RateLimitError("tool request temporarily rate limited", response=httpx.Response(429, request=httpx.Request("POST", "https://example.invalid")), body=None)
            raise TimeoutError("tool request timed out")

    monkeypatch.setattr("agentnexus.core.llm.select_provider", lambda *_: Provider())
    client = AgentLLM(model=f"temporary-{limited}", api_key="fixture", base_url="https://example.invalid")
    client.think([{"role": "user", "content": "hello"}], tools=[{"type": "function", "function": {"name": "echo"}}], max_attempts=1, silent=True)
    assert client.last_unsupported_capability is None
    assert "tool_calling" not in client.session_tracker.disabled_features
    assert select_strategy(client.session_tracker, client.capabilities) is CallingStrategy.NATIVE_TOOLS


def test_explicit_native_rejection_is_saved_for_a_new_client(monkeypatch):
    class Provider:
        def stream_chat(self, **payload):
            raise BadRequestError("tools are not supported", response=httpx.Response(400, request=httpx.Request("POST", "https://example.invalid")), body={"param": "tools", "code": "unsupported_parameter", "message": "tools are not supported"})

    monkeypatch.setattr("agentnexus.core.llm.select_provider", lambda *_: Provider())
    client = AgentLLM(model="reject-fixture", api_key="fixture", base_url="https://example.invalid")
    client.think([{"role": "user", "content": "hello"}], tools=[{"type": "function", "function": {"name": "echo"}}], max_attempts=1, silent=True)
    assert client.last_unsupported_capability == "tool_calling"
    restored = AgentLLM(model="reject-fixture", api_key="fixture", base_url="https://example.invalid")
    assert select_strategy(restored.session_tracker, restored.capabilities) is CallingStrategy.JSON_MODE


def test_native_tool_success_is_saved_without_discovery(monkeypatch):
    class Provider:
        def stream_chat(self, **payload):
            return StreamResult(tool_calls=[{"id": "native", "name": "echo", "arguments": {}}])

    monkeypatch.setattr("agentnexus.core.llm.select_provider", lambda *_: Provider())
    client = AgentLLM(model="success-fixture", api_key="fixture", base_url="https://example.invalid")
    client.think([{"role": "user", "content": "hello"}], tools=[{"type": "function", "function": {"name": "echo"}}], max_attempts=1, silent=True)
    restored = AgentLLM(model="success-fixture", api_key="fixture", base_url="https://example.invalid")
    assert restored.capability_snapshot().source == "probe"
    assert restored.capabilities.supports_tool_calling is True


@pytest.mark.parametrize("reject", [False, True])
def test_wire_evidence_stays_with_the_model_that_was_called(monkeypatch, reject):
    client = AgentLLM(model=f"old-{reject}", api_key="fixture", base_url="https://example.invalid")

    class Provider:
        def stream_chat(self, **payload):
            client.configure(model=f"new-{reject}", api_key="fixture", base_url="https://example.invalid")
            if reject:
                raise BadRequestError("tools are not supported", response=httpx.Response(400, request=httpx.Request("POST", "https://example.invalid")), body={"param": "tools", "code": "unsupported_parameter"})
            return StreamResult(tool_calls=[{"id": "native", "name": "echo", "arguments": {}}])

    monkeypatch.setattr("agentnexus.core.llm.select_provider", lambda *_: Provider())
    client.think([{"role": "user", "content": "hello"}], tools=[{"type": "function", "function": {"name": "echo"}}], max_attempts=1, silent=True)
    previous = AgentLLM(model=f"old-{reject}", api_key="fixture", base_url="https://example.invalid")
    assert previous.capability_snapshot().source == "probe"
    assert previous.capabilities.supports_tool_calling is not reject
    assert client.capability_snapshot().source == "unknown"
    assert client.session_tracker.disabled_features == set()


def test_optional_request_parameter_rejection_keeps_native_protocol(monkeypatch):
    requests = []

    class Provider:
        def stream_chat(self, **payload):
            requests.append(payload)
            if payload.get("stream_options"):
                raise BadRequestError("stream_options is not supported", response=httpx.Response(400, request=httpx.Request("POST", "https://example.invalid")), body={"param": "stream_options", "code": "unsupported_parameter"})
            return StreamResult(tool_calls=[{"id": "native", "name": "echo", "arguments": {}}])

    monkeypatch.setattr("agentnexus.core.llm.select_provider", lambda *_: Provider())
    monkeypatch.setattr("agentnexus.core.llm.time.sleep", lambda _: pytest.fail("Negotiation must not sleep like a transport retry"))
    client = AgentLLM(model="optional-fixture", api_key="fixture", base_url="https://example.invalid")
    client.think([{"role": "user", "content": "hello"}], tools=[{"type": "function", "function": {"name": "echo"}}], max_attempts=2, silent=True)
    assert len(requests) == 2
    assert requests[1]["tools"][0]["function"]["name"] == "echo"
    assert requests[1]["stream_options"] is None
    assert client.last_unsupported_capability is None
    assert client.capabilities.supports_tool_calling is True


def test_actual_requests_descend_through_protocols_on_explicit_rejections(monkeypatch):
    requests = []

    class Provider:
        def stream_chat(self, **payload):
            requests.append(payload)
            param = "tools" if payload.get("tools") else "response_format" if payload.get("response_format") else None
            if param:
                raise BadRequestError(f"{param} is not supported", response=httpx.Response(400, request=httpx.Request("POST", "https://example.invalid")), body={"param": param, "code": "unsupported_parameter"})
            return StreamResult(text='{"answer":"fallback completed"}')

    monkeypatch.setattr("agentnexus.core.llm.select_provider", lambda *_: Provider())
    registry = ToolRegistry()
    registry.register(ToolMeta(name="echo", description="Echo input", param_schema={"type": "object"}), lambda: "echo")
    client = AgentLLM(model="ladder-fixture", api_key="fixture", base_url="https://example.invalid")
    result = ReActAgent(client, registry, max_steps=4, output=lambda _: None).run("Say hello")
    assert result.answer == "fallback completed"
    assert len(requests) == 3
    assert requests[0]["tools"] and requests[0]["response_format"] is None
    assert requests[1]["tools"] is None and requests[1]["response_format"] == {"type": "json_object"}
    assert requests[2]["tools"] is None and requests[2]["response_format"] is None
