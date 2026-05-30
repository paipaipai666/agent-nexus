"""Tool execution coordination helpers for ReActAgent."""

from __future__ import annotations

import logging
import traceback
from typing import Any, Callable

from agentnexus.core.hooks import HookType, get_hook_manager

logger = logging.getLogger(__name__)


def _log_tool_error(name: str, exc: Exception) -> None:
    """Write full traceback to tool_errors.log in the agentnexus home dir."""
    try:
        from agentnexus.core.config import _config_dir
        log_path = _config_dir() / "tool_errors.log"
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"\n{'='*60}\n")
            f.write(f"Tool: {name}\n")
            f.write(f"Error: {exc}\n")
            f.write(f"Traceback:\n{traceback.format_exc()}\n")
    except Exception:
        pass


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
        return f"[{hook_ctx.abort_code}] {hook_ctx.abort_reason}"
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
        _log_tool_error(name, exc)
        return f"错误: 工具 '{name}' 执行失败: {exc}"
