"""Runtime coordination helpers for ReActAgent FSM handlers."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from agentnexus.agents.react_types import (
    ExecutionContext,
    ReActEvent,
    ReActEventType,
)
from agentnexus.tools.dispatcher import ToolDispatcher
from agentnexus.tools.result_format import summarize_tool_result


def ensure_tool_call_ids(calls: list[dict], *, step: int = 0) -> list[dict]:
    """Guarantee every tool call has a non-empty unique id.

    OpenAI-compatible backends require assistant.tool_calls[].id ↔
    tool.tool_call_id pairing; empty/missing ids are rejected as
    ``missing field tool_call_id`` (strict serde gateways). Models and the
    JSON protocol often omit ids — synthesize ``call_{step}_{i}`` locally.
    """
    out: list[dict] = []
    seen: set[str] = set()
    for i, tc in enumerate(calls):
        tc = dict(tc)
        tid = str(tc.get("id") or "").strip()
        if not tid or tid in seen:
            tid = f"call_{step}_{i}"
            suffix = 0
            while tid in seen:
                suffix += 1
                tid = f"call_{step}_{i}_{suffix}"
        tc["id"] = tid
        seen.add(tid)
        out.append(tc)
    return out


def record_native_tool_calls(
    ctx: ExecutionContext,
    *,
    thought: str,
    reasoning_content: str,
    output: Callable[[str], None],
) -> None:
    memory_state = ctx.memory_state
    tool_state = ctx.tool_state
    step = ctx.steps[-1]
    tool_state.pending_tool_calls = ensure_tool_call_ids(
        list(tool_state.pending_tool_calls), step=step.step_id
    )
    step.tool_calls = list(tool_state.pending_tool_calls)
    if thought:
        output(f"思考: {thought}")
    if memory_state.memory_manager:
        memory_state.memory_manager.append("assistant", thought)

    assistant_msg: dict = {"role": "assistant", "content": thought}
    if reasoning_content:
        assistant_msg["reasoning_content"] = reasoning_content
    assistant_tool_calls = []
    for tc in tool_state.pending_tool_calls:
        assistant_tool_calls.append({
            "id": tc.get("id", ""),
            "type": "function",
            "function": {
                "name": tc["name"],
                "arguments": json.dumps(tc["arguments"], ensure_ascii=False),
            },
        })
    if assistant_tool_calls:
        assistant_msg["tool_calls"] = assistant_tool_calls
    ctx.messages.append(assistant_msg)


def record_tool_done(ctx: ExecutionContext, payload: dict) -> None:
    memory_state = ctx.memory_state
    tool_state = ctx.tool_state
    step = ctx.steps[-1]
    rendered_result = summarize_tool_result(payload.get("result", ""))
    step.tool_outputs.append({
        "tool": payload.get("name", ""),
        "output": rendered_result,
    })
    if payload.get("name") == "subagent_run":
        try:
            raw_result = payload.get("result", "")
            if isinstance(raw_result, str):
                tool_state.last_subagent_payload = json.loads(raw_result)
            else:
                tool_state.last_subagent_payload = raw_result
        except Exception:
            tool_state.last_subagent_payload = None

    if not memory_state.memory_manager:
        return

    tool_name = payload.get("name", "")
    arguments = payload.get("arguments", {})
    result = payload.get("result", "")
    memory_state.memory_manager.append(
        "tool",
        f"Action: {tool_name}[{json.dumps(arguments, ensure_ascii=False)}]\n"
        f"Observation: {rendered_result}",
    )
    if tool_name in ("read", "file_read", "file_read_text"):
        filepath = arguments.get("file_path", arguments.get("path", ""))
        if filepath:
            memory_state.memory_manager.bridge_read(str(filepath), str(result)[:5000])


def execute_pending_tools_batch(
    ctx: ExecutionContext,
    *,
    registry: Any,
    execute_tool: Callable[[str, dict], str],
    output: Callable[[str], None],
) -> ReActEvent:
    """Execute all pending tool calls in one batch using read/write dispatch.

    Read-only tools (concurrency_safe=True) run concurrently; write tools
    run sequentially.  Results are recorded in the original order.
    """
    tool_state = ctx.tool_state
    memory_state = ctx.memory_state
    run_state = ctx.run_state

    if not tool_state.pending_tool_calls:
        if memory_state.memory_manager and memory_state.memory_manager.has_new_memories():
            memory_state.memory_context = memory_state.memory_manager.refresh_ltm_context(run_state.question)
        run_state.json_retries = 0
        return ReActEvent(ReActEventType.ALL_TOOLS_DONE)

    calls = ensure_tool_call_ids(
        list(tool_state.pending_tool_calls), step=run_state.current_step
    )
    tool_state.pending_tool_calls = []

    # Emit actions for all tools
    for tc in calls:
        output(f"行动: {tc['name']}({', '.join(f'{k}={v}' for k, v in tc['arguments'].items())})")
        ctx.emit(ReActEventType.TOOL_START, name=tc["name"], arguments=tc["arguments"], id=tc.get("id", ""))

    # Dispatch with read/write partitioning
    dispatcher = ToolDispatcher(registry)
    results = dispatcher.execute(calls, execute_fn=execute_tool)

    # Record results in original order
    for tc, result_obj in zip(calls, results):
        observation = result_obj.result if result_obj.error is None else result_obj.error
        rendered_observation = summarize_tool_result(observation)
        output(f"观察: {rendered_observation}")

        ctx.messages.append({
            "role": "tool",
            "tool_call_id": tc.get("id", ""),
            "content": rendered_observation,
        })

        record_tool_done(ctx, {
            "name": tc["name"],
            "arguments": tc["arguments"],
            "result": observation,
            "id": tc.get("id", ""),
        })
        ctx.emit(ReActEventType.TOOL_DONE, name=tc["name"], arguments=tc["arguments"], result=observation, id=tc.get("id", ""))

    if memory_state.memory_manager and memory_state.memory_manager.has_new_memories():
        memory_state.memory_context = memory_state.memory_manager.refresh_ltm_context(run_state.question)

    run_state.json_retries = 0
    return ReActEvent(ReActEventType.ALL_TOOLS_DONE)
