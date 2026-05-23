"""Performance tests for ReAct agent — step time, full run, executor creation."""

from __future__ import annotations

import random
import time
from typing import Any

import pytest

from agentnexus.tools.tool_executor import ToolExecutor

AGENT_STEP_P95_MAX_MS = 200
AGENT_FULL_RUN_P95_MAX_MS = 1000
TOOL_EXECUTOR_CREATE_P95_MAX_MS = 50


def _prepare_llm(mock: Any) -> Any:
    """Add attributes ReActAgent reads from llm_client."""
    mock.last_error = None
    mock.last_reasoning_content = None
    mock.last_tool_calls = None
    mock.last_usage = {}
    if not hasattr(mock, "model"):
        mock.model = "mock-model"
    return mock


def _noop_tool(msg: str = "") -> str:
    return f"echo: {msg}"


# ── ToolExecutor creation ─────────────────────────────────────


def test_tool_executor_creation(benchmark):
    from agentnexus.tools import register_all_tools

    def _create():
        ex = ToolExecutor()
        register_all_tools(ex, non_interactive=True)
        return ex

    executor = benchmark(_create)
    assert executor is not None


# ── Agent single step ─────────────────────────────────────────


@pytest.mark.parametrize("mock_llm_latency", ["fast"], indirect=True)
def test_agent_single_step_mock(benchmark, mock_llm_latency, perf_env):
    from agentnexus.agents.re_act_agent import ReActAgent
    from agentnexus.tools import register_all_tools

    _prepare_llm(mock_llm_latency)

    executor = ToolExecutor()
    register_all_tools(executor, non_interactive=True)

    agent = ReActAgent(
        llm_client=mock_llm_latency,
        tool_executor=executor,
        max_steps=1,
    )

    def _run_step():
        return agent.run("say hello")

    result = benchmark(_run_step)
    assert result is not None
    assert mock_llm_latency.call_count >= 1


# ── Agent multi-step ──────────────────────────────────────────


class _StatefulMock:
    """Mock LLM that returns a tool call on first think(), then an answer."""

    def __init__(self, profile: dict[str, Any]):
        self.profile = profile
        self.call_index = 0
        self.call_count = 0
        self.last_error = None
        self.last_reasoning_content = None
        self.last_tool_calls: list | None = None
        self.last_usage: dict = {}
        self._caps = None

    def think(self, *args: Any, **kwargs: Any) -> str:
        self.call_index += 1
        self.call_count = self.call_index
        delay = self.profile["base_delay"]
        delay += random.uniform(0, self.profile["jitter"])
        time.sleep(delay)

        if self.call_index == 1:
            self.last_tool_calls = [
                {
                    "name": "echo",
                    "id": "call_1",
                    "arguments": {"msg": "hello"},
                    "type": "function",
                },
            ]
            return "I will use the echo tool"
        self.last_tool_calls = None
        return '{"answer": "test complete"}'

    @property
    def capabilities(self):
        if self._caps is None:
            from unittest.mock import MagicMock
            m = MagicMock()
            m.supports_thinking = True
            m.supports_tool_calling = True
            self._caps = m
        return self._caps

    @property
    def last_truncated(self) -> bool:
        return False

    @property
    def model(self) -> str:
        return "mock-model"


def test_agent_multi_step(benchmark, perf_env):
    from agentnexus.agents.re_act_agent import ReActAgent

    executor = ToolExecutor()
    executor.registerTool(
        "echo", "Echo testing tool", _noop_tool,
        param_schema={
            "type": "object",
            "properties": {"msg": {"type": "string"}},
            "required": ["msg"],
        },
        risk_level="low",
    )

    mock_llm = _StatefulMock({
        "base_delay": 0.05,
        "jitter": 0.01,
    })

    agent = ReActAgent(
        llm_client=mock_llm,
        tool_executor=executor,
        max_steps=3,
    )

    def _run():
        return agent.run("use the echo tool")

    result = benchmark(_run)
    assert result is not None
    assert mock_llm.call_count >= 2, f"Expected ≥2 LLM calls, got {mock_llm.call_count}"


class _MultiStepMock:
    """Mock LLM that returns tool calls for N think() rounds, then an answer."""

    def __init__(self, profile: dict[str, Any], tool_steps: int = 5):
        self.profile = profile
        self.tool_steps = tool_steps
        self.call_index = 0
        self.call_count = 0
        self.last_error = None
        self.last_reasoning_content = None
        self.last_tool_calls: list | None = None
        self.last_usage: dict = {}
        self._caps = None

    def think(self, *args: Any, **kwargs: Any) -> str:
        self.call_index += 1
        self.call_count = self.call_index
        delay = self.profile["base_delay"]
        delay += random.uniform(0, self.profile["jitter"])
        time.sleep(delay)

        if self.call_index <= self.tool_steps:
            self.last_tool_calls = [
                {
                    "name": "echo",
                    "id": f"call_{self.call_index}",
                    "arguments": {"msg": f"step_{self.call_index}"},
                    "type": "function",
                },
            ]
            return f"Step {self.call_index}"
        self.last_tool_calls = None
        return '{"answer": "test complete"}'

    @property
    def capabilities(self):
        if self._caps is None:
            from unittest.mock import MagicMock
            m = MagicMock()
            m.supports_thinking = True
            m.supports_tool_calling = True
            self._caps = m
        return self._caps

    @property
    def last_truncated(self) -> bool:
        return False

    @property
    def model(self) -> str:
        return "mock-model"


@pytest.mark.parametrize("steps", [5, 10])
def test_agent_multi_tool_steps(benchmark, perf_env, steps):
    from agentnexus.agents.re_act_agent import ReActAgent

    executor = ToolExecutor()
    executor.registerTool(
        "echo", "Echo testing tool", _noop_tool,
        param_schema={
            "type": "object",
            "properties": {"msg": {"type": "string"}},
            "required": ["msg"],
        },
        risk_level="low",
    )

    mock_llm = _MultiStepMock({
        "base_delay": 0.05,
        "jitter": 0.01,
    }, tool_steps=steps)

    agent = ReActAgent(
        llm_client=mock_llm,
        tool_executor=executor,
        max_steps=steps + 1,
    )

    def _run():
        return agent.run("use the echo tool repeatedly")

    result = benchmark(_run)
    assert result is not None
    assert mock_llm.call_count >= steps + 1, (
        f"Expected ≥{steps + 1} LLM calls, got {mock_llm.call_count}"
    )


# ── ToolExecutor invoke overhead ──────────────────────────────


def test_tool_executor_invoke_overhead(benchmark):
    ex = ToolExecutor()
    ex.registerTool(
        "echo", "Echo testing tool", _noop_tool,
        param_schema={
            "type": "object",
            "properties": {"msg": {"type": "string"}},
            "required": ["msg"],
        },
        risk_level="low",
    )

    benchmark(ex.registry.invoke, "echo", {"msg": "hello"}, "test_agent")


# ── Registry audit overhead ───────────────────────────────────


def test_registry_audit_overhead(benchmark, perf_env):
    from agentnexus.tools.registry import ToolMeta, ToolRegistry

    r = ToolRegistry()
    meta = ToolMeta(
        name="echo",
        description="Echo testing tool",
        param_schema={
            "type": "object",
            "properties": {"msg": {"type": "string"}},
        },
    )
    r.register(meta, _noop_tool)

    for i in range(100):
        r.invoke("echo", {"msg": f"hello {i}"})

    def _list_audit():
        return r._audit_log[-10:]

    benchmark(_list_audit)
