"""LLM call parameter preparation for ReAct strategies."""

from __future__ import annotations

from typing import Any, Callable

from agentnexus.agents.react_types import CallingStrategy

# Markers checked to detect whether the JSON format section is already appended
# to the last message. The garbled twin is the mojibake form that can appear in
# content corrupted by historical encoding damage; matching must cover BOTH
# forms. Do NOT normalize the encoding here — that would change behavior.
_JSON_SECTION_MARKER = "== 输出格式"
_JSON_SECTION_MARKER_MOJIBAKE = "== 杈撳嚭鏍煎紡"


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


_STRATEGY_RESULT = tuple[list[dict] | None, dict[str, str] | None]


def _apply_native_tools(messages, tools, json_format_section) -> _STRATEGY_RESULT:
    return tools, None


def _apply_json_mode(messages, tools, json_format_section) -> _STRATEGY_RESULT:
    return None, {"type": "json_object"}


def _apply_prompt_json(messages, tools, json_format_section) -> _STRATEGY_RESULT:
    last_msg = messages[-1]
    section = json_format_section or build_json_format_section()
    content = last_msg.get("content", "")
    if _JSON_SECTION_MARKER not in content and _JSON_SECTION_MARKER_MOJIBAKE not in content:
        last_msg["content"] += "\n\n" + section
    return None, None


def _apply_noop(messages, tools, json_format_section) -> _STRATEGY_RESULT:
    return None, None


_STRATEGY_APPLIERS: dict[CallingStrategy, Callable[..., _STRATEGY_RESULT]] = {
    CallingStrategy.NATIVE_TOOLS: _apply_native_tools,
    CallingStrategy.JSON_MODE: _apply_json_mode,
    CallingStrategy.PROMPT_JSON: _apply_prompt_json,
    CallingStrategy.PLAIN_TEXT: _apply_noop,
}


def prepare_llm_call(
    strategy: CallingStrategy,
    messages: list[dict],
    tools: list[dict],
    *,
    json_format_section: str | None = None,
) -> _STRATEGY_RESULT:
    return _STRATEGY_APPLIERS.get(strategy, _apply_noop)(messages, tools, json_format_section)


def call_llm(llm_client: Any, ctx, *, json_format_section: str | None = None,
             on_token: Any = None, effort: str | None = None) -> str:
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
        silent=True,
        effort=effort,
    )

    # ── after model hook (can modify response text) ──────────
    hook_ctx = hook_mgr.fire(HookType.AFTER_MODEL_CALL, {
        "messages": ctx.messages,
        "response_text": result,
    })
    return hook_ctx.payload.get("response_text", result)
