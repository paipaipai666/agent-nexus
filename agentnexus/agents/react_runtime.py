"""Runtime coordination helpers for ReActAgent FSM handlers."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from agentnexus.agents.react_types import AgentStep, CallingStrategy, ExecutionContext, ReActEvent, ReActEventType


def record_llm_response(
    ctx: ExecutionContext,
    *,
    response_text: str,
    llm_client: Any,
) -> list[ReActEvent]:
    run_state = ctx.run_state
    memory_state = ctx.memory_state
    tool_state = ctx.tool_state
    step = AgentStep(
        step_id=run_state.current_step,
        strategy_used=run_state.strategy,
        reasoning_content=llm_client.last_reasoning_content,
        content=response_text,
    )
    ctx.steps.append(step)

    cur = getattr(llm_client, "last_usage", {})
    if isinstance(cur, dict):
        ctx._total_usage["input_tokens"] += cur.get("input_tokens", 0)
        ctx._total_usage["output_tokens"] += cur.get("output_tokens", 0)

    if memory_state.memory_manager:
        memory_state.memory_manager.mark_api_call()

    ctx.last_response_text = response_text
    ctx.last_reasoning = llm_client.last_reasoning_content or ""

    if run_state.strategy == CallingStrategy.NATIVE_TOOLS:
        tool_calls = llm_client.last_tool_calls
        tool_state.pending_tool_calls = tool_calls if isinstance(tool_calls, list) else []
        return [ReActEvent(ReActEventType.ROUTE_NATIVE)]
    return [ReActEvent(ReActEventType.ROUTE_JSON)]


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
    step.tool_calls = list(tool_state.pending_tool_calls)
    output(f"思考: {thought}")
    ctx.emit(
        ReActEventType.TOOLS_FOUND,
        thought=thought,
        tool_calls=list(tool_state.pending_tool_calls),
    )
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
    step.tool_outputs.append({
        "tool": payload.get("name", ""),
        "output": payload.get("result", ""),
    })
    if payload.get("name") == "subagent_run":
        try:
            tool_state.last_subagent_payload = json.loads(payload.get("result", ""))
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
        f"Observation: {result}",
    )
    if tool_name in ("read", "file_read", "file_read_text"):
        filepath = arguments.get("file_path", arguments.get("path", ""))
        if filepath:
            memory_state.memory_manager.bridge_read(str(filepath), str(result)[:5000])


def execute_pending_tool(
    ctx: ExecutionContext,
    *,
    execute_tool: Callable[[str, dict], str],
    output: Callable[[str], None],
) -> ReActEvent:
    run_state = ctx.run_state
    memory_state = ctx.memory_state
    tool_state = ctx.tool_state
    if not tool_state.pending_tool_calls:
        if memory_state.memory_manager and memory_state.memory_manager.has_new_memories():
            memory_state.memory_context = memory_state.memory_manager.refresh_ltm_context(run_state.question)
        run_state.json_retries = 0
        return ReActEvent(ReActEventType.ALL_TOOLS_DONE)

    tool_call = tool_state.pending_tool_calls.pop(0)
    output(f"行动: {tool_call['name']}({', '.join(f'{k}={v}' for k, v in tool_call['arguments'].items())})")
    ctx.emit(ReActEventType.TOOL_START, name=tool_call["name"], arguments=tool_call["arguments"])
    observation = execute_tool(tool_call["name"], tool_call["arguments"])
    output(f"观察: {observation}")

    ctx.messages.append({
        "role": "tool",
        "tool_call_id": tool_call.get("id", ""),
        "content": str(observation),
    })

    return ReActEvent(
        ReActEventType.TOOL_DONE,
        {
            "name": tool_call["name"],
            "arguments": tool_call["arguments"],
            "result": observation,
            "id": tool_call.get("id", ""),
        },
    )


def execute_json_tool_call(
    ctx: ExecutionContext,
    *,
    parsed: dict,
    thought: str,
    execute_tool: Callable[[str, dict], str],
    output: Callable[[str], None],
) -> list[ReActEvent]:
    run_state = ctx.run_state
    memory_state = ctx.memory_state
    tool_state = ctx.tool_state
    step = ctx.steps[-1]
    step.tool_calls = [{"name": parsed["tool"], "arguments": parsed["params"]}]

    if thought:
        output(f"思考: {thought}")
        ctx.emit(ReActEventType.TOOLS_FOUND, thought=thought, tool_calls=list(step.tool_calls))

    output(f"行动: {parsed['tool']}({', '.join(f'{k}={v}' for k, v in parsed['params'].items())})")
    ctx.emit(ReActEventType.TOOL_START, name=parsed["tool"], arguments=parsed["params"])
    observation = execute_tool(parsed["tool"], parsed["params"])
    output(f"观察: {observation}")
    ctx.emit(ReActEventType.TOOL_DONE, name=parsed["tool"], arguments=parsed["params"], result=observation, id="")

    step.tool_outputs.append({"tool": parsed["tool"], "output": observation})
    if parsed["tool"] == "subagent_run":
        try:
            tool_state.last_subagent_payload = json.loads(observation)
        except Exception:
            tool_state.last_subagent_payload = None

    ctx.messages.append({"role": "assistant", "content": ctx.last_response_text})
    ctx.messages.append({
        "role": "user",
        "content": (
            f"工具执行结果:\n{observation}\n\n请根据结果继续。如果信息充分，输出最终答案。\n"
            f"格式: {{\"answer\": \"你的回答\"}}"
        ),
    })

    if memory_state.memory_manager:
        memory_state.memory_manager.append("assistant", ctx.last_response_text)
        memory_state.memory_manager.append(
            "tool",
            f"Action: {parsed['tool']}[{json.dumps(parsed['params'], ensure_ascii=False)}]\n"
            f"Observation: {observation}",
        )
        if memory_state.memory_manager.has_new_memories():
            memory_state.memory_context = memory_state.memory_manager.refresh_ltm_context(run_state.question)

    run_state.json_retries = 0
    return [ReActEvent(ReActEventType.ALL_TOOLS_DONE)]


def retry_gate(ctx: ExecutionContext, reason: str) -> list[ReActEvent]:
    run_state = ctx.run_state
    if run_state.json_retries < run_state.max_json_retries:
        return [ReActEvent(ReActEventType.RETRIES_LEFT, {"reason": reason})]
    if run_state.strategy == CallingStrategy.JSON_MODE:
        return [ReActEvent(ReActEventType.NO_RETRIES, {"reason": reason})]
    return [ReActEvent(ReActEventType.FALLBACK_TEXT, {"reason": reason})]
