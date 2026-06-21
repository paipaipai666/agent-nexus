"""Test that batch tool execution emits TOOL_DONE events for each tool.

Bug: execute_pending_tools_batch calls record_tool_done() (direct function
call) instead of ctx.emit(TOOL_DONE), so the event bridge never sees
individual TOOL_DONE events. The GUI shows "Calling: X" forever.
"""

import pytest
from unittest.mock import MagicMock

from agentnexus.agents.react_types import (
    AgentStep,
    CallingStrategy,
    ExecutionContext,
    ReActEvent,
    ReActEventType,
    RunState,
    MemoryRetrievalState,
    ToolCallState,
)


# ── helpers ──────────────────────────────────────────────────────────

def _make_ctx(*tool_names: str) -> ExecutionContext:
    """Build an ExecutionContext with pending tool calls for the given names."""
    pending = [
        {"name": name, "arguments": {"query": f"test_{name}"}, "id": f"call_{name}"}
        for name in tool_names
    ]
    ctx = ExecutionContext(
        question="test question",
        messages=[],
        current_step=0,
        strategy=CallingStrategy.NATIVE_TOOLS,
        steps=[AgentStep(step_id=0, strategy_used=CallingStrategy.NATIVE_TOOLS, content="")],
        pending_tool_calls=pending,
    )
    return ctx


def _make_registry() -> MagicMock:
    """Return a mock ToolRegistry where all tools are NOT concurrency-safe."""
    registry = MagicMock()
    registry.get_meta.return_value = MagicMock(concurrency_safe=False)
    return registry


def _capture_events(ctx: ExecutionContext) -> list[ReActEvent]:
    """Install an event collector on ctx and return the list."""
    events: list[ReActEvent] = []
    ctx._on_emit = lambda evt, _from, _to: events.append(evt)
    return events


def _noop_output(_msg: str) -> None:
    pass


# ── tests ────────────────────────────────────────────────────────────

def test_batch_emits_tool_done_for_each_tool():
    """RED: batch path must emit TOOL_DONE for each tool, like the single path does."""
    from agentnexus.agents.react_runtime import execute_pending_tools_batch

    ctx = _make_ctx("web_search", "memory_search")
    events = _capture_events(ctx)
    execute_fn = MagicMock(side_effect=lambda name, args: f"result_of_{name}")

    execute_pending_tools_batch(
        ctx,
        registry=_make_registry(),
        execute_tool=execute_fn,
        output=_noop_output,
    )

    tool_done_events = [e for e in events if e.type == ReActEventType.TOOL_DONE]
    tool_start_events = [e for e in events if e.type == ReActEventType.TOOL_START]

    # TOOL_START events should be emitted (this already works)
    assert len(tool_start_events) == 2, (
        f"Expected 2 TOOL_START events, got {len(tool_start_events)}"
    )

    # TOOL_DONE events should be emitted per tool (this is the bug)
    assert len(tool_done_events) == 2, (
        f"Expected 2 TOOL_DONE events, got {len(tool_done_events)}. "
        "Batch path does not emit TOOL_DONE — GUI shows 'Calling: X' forever."
    )


def test_batch_tool_done_events_have_correct_payload():
    """RED: TOOL_DONE events must carry name, arguments, and result."""
    from agentnexus.agents.react_runtime import execute_pending_tools_batch

    ctx = _make_ctx("web_search", "memory_search")
    events = _capture_events(ctx)
    execute_fn = MagicMock(side_effect=lambda name, args: f"result_of_{name}")

    execute_pending_tools_batch(
        ctx,
        registry=_make_registry(),
        execute_tool=execute_fn,
        output=_noop_output,
    )

    tool_done_events = [e for e in events if e.type == ReActEventType.TOOL_DONE]
    assert len(tool_done_events) == 2

    names = {e.payload["name"] for e in tool_done_events}
    assert names == {"web_search", "memory_search"}

    for e in tool_done_events:
        assert "result" in e.payload, f"TOOL_DONE for {e.payload['name']} missing 'result'"
        assert e.payload["result"] == f"result_of_{e.payload['name']}"


def test_batch_tool_done_events_preserve_order():
    """RED: TOOL_DONE events should be in the same order as the pending calls."""
    from agentnexus.agents.react_runtime import execute_pending_tools_batch

    ctx = _make_ctx("alpha_tool", "beta_tool", "gamma_tool")
    events = _capture_events(ctx)
    execute_fn = MagicMock(side_effect=lambda name, args: f"result_of_{name}")

    execute_pending_tools_batch(
        ctx,
        registry=_make_registry(),
        execute_tool=execute_fn,
        output=_noop_output,
    )

    tool_done_events = [e for e in events if e.type == ReActEventType.TOOL_DONE]
    assert len(tool_done_events) == 3

    done_names = [e.payload["name"] for e in tool_done_events]
    assert done_names == ["alpha_tool", "beta_tool", "gamma_tool"]


def test_single_tool_returns_tool_done_fsm_event():
    """Sanity: the single-tool path returns TOOL_DONE as an FSM event (processed by FSM loop)."""
    from agentnexus.agents.react_runtime import execute_pending_tool

    ctx = _make_ctx("web_search")
    execute_fn = MagicMock(return_value="search_result")

    result = execute_pending_tool(
        ctx,
        execute_tool=execute_fn,
        output=_noop_output,
    )

    # Single-tool path returns TOOL_DONE as FSM event (not via ctx.emit)
    assert isinstance(result, ReActEvent)
    assert result.type == ReActEventType.TOOL_DONE
    assert result.payload["name"] == "web_search"
    assert result.payload["result"] == "search_result"


def test_batch_still_records_tool_outputs_in_step():
    """Verify record_tool_done still populates step.tool_outputs (regression)."""
    from agentnexus.agents.react_runtime import execute_pending_tools_batch

    ctx = _make_ctx("web_search", "memory_search")
    _capture_events(ctx)
    execute_fn = MagicMock(side_effect=lambda name, args: f"result_of_{name}")

    execute_pending_tools_batch(
        ctx,
        registry=_make_registry(),
        execute_tool=execute_fn,
        output=_noop_output,
    )

    step = ctx.steps[-1]
    assert len(step.tool_outputs) == 2
    names = [o["tool"] for o in step.tool_outputs]
    assert "web_search" in names
    assert "memory_search" in names


def test_batch_returns_all_tools_done_event():
    """The return value should still be ALL_TOOLS_DONE (FSM control flow)."""
    from agentnexus.agents.react_runtime import execute_pending_tools_batch

    ctx = _make_ctx("web_search")
    _capture_events(ctx)
    execute_fn = MagicMock(return_value="result")

    result = execute_pending_tools_batch(
        ctx,
        registry=_make_registry(),
        execute_tool=execute_fn,
        output=_noop_output,
    )

    assert isinstance(result, ReActEvent)
    assert result.type == ReActEventType.ALL_TOOLS_DONE
