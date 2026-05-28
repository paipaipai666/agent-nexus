"""LLM call parameter preparation for ReAct strategies."""

from __future__ import annotations

from typing import Any

from agentnexus.agents.react_types import CallingStrategy


def build_json_format_section() -> str:
    return (
        "== 输出格式（严格遵守）==\n"
        "你必须在每次回复中输出合法的 JSON 对象。\n\n"
        "调用工具时:\n"
        '{"thought": "1-3句简洁分析，说明意图和依据", "tool": "工具名", "params": {"参数名": "值", ...}}\n\n'
        "给出最终答案时:\n"
        '{"answer": "你的完整回答"}\n\n'
        "答案中的换行用 \\n 表示，双引号用 \\\" 转义。"
    )


def prepare_llm_call(
    strategy: CallingStrategy,
    messages: list[dict],
    tools: list[dict],
    *,
    json_format_section: str | None = None,
) -> tuple[list[dict] | None, dict[str, str] | None]:
    if strategy == CallingStrategy.NATIVE_TOOLS:
        return tools, None
    if strategy == CallingStrategy.JSON_MODE:
        return None, {"type": "json_object"}
    if strategy == CallingStrategy.PROMPT_JSON:
        last_msg = messages[-1]
        section = json_format_section or build_json_format_section()
        if "== 输出格式" not in last_msg.get("content", "") and "== 杈撳嚭鏍煎紡" not in last_msg.get("content", ""):
            last_msg["content"] += "\n\n" + section
    return None, None


def call_llm(llm_client: Any, ctx, *, json_format_section: str | None = None,
             on_token: Any = None) -> str:
    from agentnexus.core.hooks import HookType, get_hook_manager

    hook_mgr = get_hook_manager()
    run_state = ctx.run_state
    memory_state = ctx.memory_state
    tool_state = ctx.tool_state

    # ── before model hook (can modify messages) ──────────────
    hook_ctx = hook_mgr.fire(HookType.BEFORE_MODEL_CALL, {
        "messages": ctx.messages,
        "tools": tool_state.tools,
        "strategy": run_state.strategy.name,
    })
    if hook_ctx.aborted:
        return hook_ctx.payload.get("response_text", "")
    ctx.messages = hook_ctx.payload.get("messages", ctx.messages)

    think_tools, think_rfmt = prepare_llm_call(
        run_state.strategy,
        ctx.messages,
        tool_state.tools,
        json_format_section=json_format_section,
    )
    projection_fn = memory_state.memory_manager.build_projection if memory_state.memory_manager else None
    result = llm_client.think(
        messages=ctx.messages,
        tools=think_tools,
        response_format=think_rfmt,
        projection_fn=projection_fn,
        thinking=run_state.thinking_enabled,
        on_token=on_token,
    )

    # ── after model hook (can modify response text) ──────────
    hook_ctx = hook_mgr.fire(HookType.AFTER_MODEL_CALL, {
        "messages": ctx.messages,
        "response_text": result,
    })
    return hook_ctx.payload.get("response_text", result)
