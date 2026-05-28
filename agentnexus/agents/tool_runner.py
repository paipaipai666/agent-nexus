"""Tool execution coordination helpers for ReActAgent."""

from __future__ import annotations

from typing import Any, Callable

from agentnexus.core.hooks import HookType, get_hook_manager


def execute_tool(
    *,
    tool_executor: Any,
    name: str,
    arguments: dict,
    caller: str,
    hitl_approver: Callable[[str], bool],
    tool_policy: Any = None,
    cancel_checker: Callable[[], bool] | None = None,
) -> str:
    hook_mgr = get_hook_manager()

    # ── before hook (can modify params or abort) ───────────────
    hook_ctx = hook_mgr.fire(HookType.BEFORE_TOOL_CALL, {
        "name": name,
        "params": arguments,
        "caller": caller,
    })
    if hook_ctx.aborted:
        return f"[blocked] {hook_ctx.abort_reason}"
    arguments = hook_ctx.payload.get("params", arguments)

    try:
        if cancel_checker is not None and cancel_checker():
            raise RuntimeError("cancelled")
        result = tool_executor.registry.invoke(
            name=name,
            params=arguments,
            caller=caller,
            hitl_approver=hitl_approver,
            tool_policy=tool_policy,
        )

        # ── after hook (observer) ──────────────────────────────
        hook_mgr.fire(HookType.AFTER_TOOL_CALL, {
            "name": name,
            "params": arguments,
            "result": result,
        })

        if isinstance(result, dict):
            return result
        return str(result)
    except Exception as exc:
        # ── error hook (observer) ──────────────────────────────
        hook_mgr.fire(HookType.ON_TOOL_ERROR, {
            "name": name,
            "params": arguments,
            "error": exc,
        })
        return f"错误: 工具 '{name}' 执行失败: {exc}"
