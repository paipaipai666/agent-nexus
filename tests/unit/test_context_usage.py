from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentnexus.agents.llm_strategy import call_llm
from agentnexus.agents.re_act_agent import ReActAgent
from agentnexus.agents.react_types import CallingStrategy, ExecutionContext
from agentnexus.core.config import get_settings
from agentnexus.core.context_usage import ContextFragment, estimate_context_usage
from agentnexus.core.llm import AgentLLM
from agentnexus.core.providers.base import StreamResult
from agentnexus.memory.compaction_engine import CompactionEngine
from agentnexus.server.deps import get_runtime
from agentnexus.server.routes.runtime import router
from agentnexus.skills.workflow import Workflow
from agentnexus.tools.registry import ToolMeta, ToolRegistry


def test_context_breakdown_accounts_for_each_part_once():
    snapshot = estimate_context_usage(
        messages=[
            {"role": "system", "content": "hello world"},
            {"role": "user", "content": "hello"},
            {"role": "tool", "content": "world", "tool_call_id": "read"},
        ],
        tools=None,
        fragments=(ContextFragment("skills", "world"),),
        mcp_tool_names=frozenset(),
    )
    report = snapshot.to_dict(capacity=100)
    assert report["categories"] == {
        "system_prompt": 8, "tools": 0, "mcp": 0, "skills": 1,
        "memory": 0, "conversation": 5, "tool_results": 6,
    }
    assert report["used_tokens"] == 20
    assert report["free_tokens"] == 80
    assert report["estimated"] is True


@pytest.fixture
def runtime_api(monkeypatch):
    monkeypatch.setattr(get_settings(), "model_tool_calling", True)
    monkeypatch.setattr(get_settings(), "model_json_mode", True)
    registry = ToolRegistry()
    for name, source in [("read", "builtin"), ("remote", "mcp")]:
        registry.register(ToolMeta(
            name=name, source_type=source, description="hello",
            param_schema={"type": "object", "properties": {}},
        ), lambda: "world")
    llm = AgentLLM(model="test/model", api_key="test-key", base_url="http://127.0.0.1:1")
    llm.capabilities.supports_tool_calling = True
    llm.capabilities.supports_thinking = False
    monkeypatch.setattr(llm, "_call_via_provider", lambda *_args, **_kwargs: StreamResult(
        text="Done", usage={"input_tokens": 100, "output_tokens": 1},
    ))
    agent = ReActAgent(llm, registry, max_steps=2, output=lambda _text: None)
    agent.set_available_skill_context("hello")
    agent.set_mcp_context("world")
    memory = SimpleNamespace(ctx_max=1000, compact_threshold=800, estimate_stm_tokens=lambda: 100)
    runtime = SimpleNamespace(
        agent=agent, memory_manager=memory, skill=None,
        settings=SimpleNamespace(get_active_llm_profile=lambda: ("test/model",)),
        chat=SimpleNamespace(_agents={"s1": agent}, _memory_managers={"s1": memory}),
    )
    app = FastAPI()
    app.include_router(router, prefix="/api/runtime")
    app.dependency_overrides[get_runtime] = lambda: runtime
    with TestClient(app) as client:
        yield client, agent, llm, runtime
    registry.close()


def test_runtime_reports_real_prompt_and_native_mcp_schema_usage(runtime_api):
    client, agent, _llm, _runtime = runtime_api
    assert agent.run("hello").answer == "Done"
    data = client.get("/api/runtime/status", params={"session_id": "s1"}).json()
    report = data["context_usage"]
    assert report["categories"]["skills"] == 1
    assert report["categories"]["mcp"] > 1
    assert report["categories"]["tools"] > 0
    assert report["categories"]["system_prompt"] > 0
    assert report["categories"]["conversation"] > 0
    assert report["has_images"] is False
    assert data["compaction"] == {"basis": "short_term_memory", "used_tokens": 100, "threshold_tokens": 800}


@pytest.mark.parametrize("fragments, expected", [([], 47), (["security"], 84)])
def test_selected_skill_workflow_guidance_is_counted_as_skill_context(runtime_api, fragments, expected):
    client, agent, _llm, _runtime = runtime_api
    agent.set_session_profile(Workflow.model_validate({
        "id": "budget_demo", "version": "1", "display_name": "Review context",
        "prompt_profile": {"system": "react", "fragments": fragments},
        "tool_policy": {"max_risk": "low"},
        "steps": [{"type": "prompt", "prompt": "Review context."}],
        "success_criteria": ["Report usage."],
    }).to_session_profile())
    agent.run("hello")
    report = client.get("/api/runtime/status", params={"session_id": "s1"}).json()["context_usage"]
    assert report["categories"]["skills"] == expected


def test_unknown_session_does_not_show_another_agents_context(runtime_api):
    client, agent, _llm, _runtime = runtime_api
    agent.run("hello")
    data = client.get("/api/runtime/status", params={"session_id": "unknown"}).json()
    assert data["context_usage"] is None
    assert data["compaction"] is None


def test_compaction_threshold_tracks_tokens_freed_by_snipping():
    engine = CompactionEngine(SimpleNamespace())
    engine.compact_threshold = 800
    assert engine.effective_threshold == 800
    engine.snip_freed_tokens = 300
    assert engine.effective_threshold == 500
    engine.snip_freed_tokens = 900
    assert engine.effective_threshold == 400


def test_measurement_uses_projected_input_instead_of_uncompressed_history(runtime_api):
    _client, _agent, llm, _runtime = runtime_api
    ctx = ExecutionContext(question="hello", strategy=CallingStrategy.NATIVE_TOOLS)
    ctx.messages = [{"role": "user", "content": "discard " * 1000}]
    ctx.memory_state.memory_manager = SimpleNamespace(
        build_projection=lambda _messages: [{"role": "user", "content": "hello"}],
    )
    reports = []

    def observe(messages, tools):
        reports.append(estimate_context_usage(
            messages=messages, tools=tools, fragments=(), mcp_tool_names=frozenset(),
        ).to_dict(capacity=100))

    assert call_llm(llm, ctx, on_context=observe) == "Done"
    assert len(reports) == 1
    assert reports[0]["used_tokens"] == 8
    assert reports[0]["free_tokens"] == 92


def test_images_and_overflow_do_not_create_false_free_capacity():
    report = estimate_context_usage(
        messages=[{"role": "user", "content": [
            {"type": "text", "text": "hello"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," + "abc" * 1000}},
        ]}], tools=None, fragments=(), mcp_tool_names=frozenset(),
    ).to_dict(capacity=5)
    assert report["used_tokens"] == 8
    assert report["free_tokens"] == 0
    assert report["has_images"] is True


def test_repeated_text_keeps_its_own_prompt_source():
    report = estimate_context_usage(
        messages=[{"role": "system", "content": "hello"}, {"role": "system", "content": "hello hello"}],
        tools=None,
        fragments=(
            ContextFragment("skills", "hello", source=(1, "hello hello")),
            ContextFragment("mcp", "hello", source=(1, "hello hello")),
        ),
        mcp_tool_names=frozenset(),
    ).to_dict(capacity=100)
    assert report["categories"]["system_prompt"] == 12
    assert report["categories"]["skills"] == 1
    assert report["categories"]["mcp"] == 1
