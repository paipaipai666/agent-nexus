"""Tool execution coordination helpers for ReActAgent."""

from __future__ import annotations

import concurrent.futures
import logging
import time
import traceback
from typing import Any, Callable

from agentnexus.core.hooks import HookType, get_hook_manager
from agentnexus.tools.errors import ToolError, ToolErrorCode

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
    except Exception as io_err:
        logger.debug("Failed to write tool error log: %s", io_err)


def execute_tool(
    *,
    tool_executor: Any,
    name: str,
    arguments: dict,
    caller: str,
    hitl_approver: Callable[[str], bool],
    tool_policy: Any = None,
    cancel_checker: Callable[[], bool] | None = None,
) -> str | dict | ToolError:
    hook_mgr = get_hook_manager()

    # ── before hook (can modify params or abort) ───────────────
    hook_ctx = hook_mgr.fire(HookType.BEFORE_TOOL_CALL, {
        "name": name,
        "params": arguments,
        "caller": caller,
        "selection_reason": f"LLM selected '{name}' tool",
    })
    if hook_ctx.aborted:
        return ToolError(
            error_code=hook_ctx.abort_code or "EXECUTION_FAILED",
            message=str(hook_ctx.abort_reason),
            recoverable=False,
            suggested_action="Check tool policy and agent permissions",
        )
    arguments = hook_ctx.payload.get("params", arguments)

    try:
        if cancel_checker is not None and cancel_checker():
            raise RuntimeError("cancelled")
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                tool_executor.invoke,
                name=name,
                params=arguments,
                caller=caller,
                hitl_approver=hitl_approver,
                tool_policy=tool_policy,
            )
            try:
                # Poll for completion with periodic cancel checks.
                # future.result(timeout=60) would block the full 60s,
                # making cancel signals invisible during tool execution.
                deadline = time.monotonic() + 60
                while True:
                    if cancel_checker is not None and cancel_checker():
                        future.cancel()
                        raise RuntimeError("cancelled")
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        result = ToolError(
                            error_code=ToolErrorCode.TIMEOUT,
                            message=f"工具 {name} 执行超时 (60s)",
                            recoverable=True,
                            suggested_action="Retry with a longer timeout or simplify the request",
                        )
                        break
                    try:
                        result = future.result(timeout=min(remaining, 1.0))
                        break
                    except concurrent.futures.TimeoutError:
                        continue
            except concurrent.futures.TimeoutError:
                result = ToolError(
                    error_code=ToolErrorCode.TIMEOUT,
                    message=f"工具 {name} 执行超时 (60s)",
                    recoverable=True,
                    suggested_action="Retry with a longer timeout or simplify the request",
                )

        # ── after hook (observer) ──────────────────────────────
        hook_mgr.fire(HookType.AFTER_TOOL_CALL, {
            "name": name,
            "params": arguments,
            "result": result,
        })

        if isinstance(result, (dict, ToolError)):
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
        # LOW-02: Include message for safe domain exceptions, strip for generic ones
        if isinstance(exc, RuntimeError) and str(exc) == "cancelled":
            return ToolError(
                error_code=ToolErrorCode.CANCELLED,
                message=f"工具 '{name}' 调用被取消",
                recoverable=True,
                suggested_action="Retry if cancellation was unintended",
            )
        if isinstance(exc, RuntimeError) and "rate limit" in str(exc).lower():
            return ToolError(
                error_code=ToolErrorCode.RATE_LIMITED,
                message=str(exc),
                recoverable=True,
                suggested_action="Wait and retry after the rate limit window resets",
            )
        if isinstance(exc, PermissionError):
            return ToolError(
                error_code=ToolErrorCode.PERMISSION_DENIED,
                message=str(exc),
                recoverable=False,
                suggested_action="Check agent permissions and tool RBAC configuration",
            )
        if isinstance(exc, (ValueError, TypeError)):
            return ToolError(
                error_code=ToolErrorCode.VALIDATION_FAILED,
                message=str(exc),
                recoverable=False,
                suggested_action="Verify input parameters match the tool schema",
            )
        if isinstance(exc, KeyError):
            return ToolError(
                error_code=ToolErrorCode.EXECUTION_FAILED,
                message=str(exc),
                recoverable=False,
                suggested_action="Check that the requested resource exists",
            )
        return ToolError(
            error_code=ToolErrorCode.EXECUTION_FAILED,
            message=f"工具 '{name}' 执行失败",
            recoverable=False,
            suggested_action="Check tool_errors.log for details",
        )
