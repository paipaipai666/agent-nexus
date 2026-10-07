"""Tool execution coordination helpers for ReActAgent."""

from __future__ import annotations

import concurrent.futures
import logging
import threading
import traceback
from typing import Any, Callable

from agentnexus.agents.exceptions import AgentCancelled
from agentnexus.core.hooks import HookType, fire_hook
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


_ERROR_CLASSIFIERS: list[tuple[
    Callable[[Exception], bool],
    ToolErrorCode,
    Callable[[str, Exception], str],
    bool,
    str,
]] = [
    (
        lambda exc: isinstance(exc, AgentCancelled),
        ToolErrorCode.CANCELLED,
        lambda name, exc: f"工具 '{name}' 调用被取消",
        True,
        "Retry if cancellation was unintended",
    ),
    (
        # Registry-enforced ToolMeta.timeout_sec expiry.
        lambda exc: isinstance(exc, TimeoutError),
        ToolErrorCode.TIMEOUT,
        lambda name, exc: str(exc),
        True,
        "Retry with simpler request or increase tool timeout_sec",
    ),
    (
        lambda exc: isinstance(exc, RuntimeError) and "rate limit" in str(exc).lower(),
        ToolErrorCode.RATE_LIMITED,
        lambda name, exc: str(exc),
        True,
        "Wait and retry after the rate limit window resets",
    ),
    (
        lambda exc: isinstance(exc, PermissionError),
        ToolErrorCode.PERMISSION_DENIED,
        lambda name, exc: str(exc),
        False,
        "Check agent permissions and tool RBAC configuration",
    ),
    (
        lambda exc: isinstance(exc, (ValueError, TypeError)),
        ToolErrorCode.VALIDATION_FAILED,
        lambda name, exc: str(exc),
        False,
        "Verify input parameters match the tool schema",
    ),
    (
        lambda exc: isinstance(exc, KeyError),
        ToolErrorCode.EXECUTION_FAILED,
        lambda name, exc: str(exc),
        False,
        "Check that the requested resource exists",
    ),
    # default fallback
    (
        lambda exc: True,
        ToolErrorCode.EXECUTION_FAILED,
        lambda name, exc: f"工具 '{name}' 执行失败",
        False,
        "Check tool_errors.log for details",
    ),
]


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
    # ── before hook (can modify params or abort) ───────────────
    hook_ctx = fire_hook(HookType.BEFORE_TOOL_CALL, {
        "name": name,
        "params": arguments,
        "caller": caller,
        "selection_reason": f"LLM selected '{name}' tool",
    })
    if hook_ctx.aborted:
        return ToolError(
            error_code=hook_ctx.abort_code or "EXECUTION_FAILED",
            message=hook_ctx.to_feedback(),
            recoverable=False,
            suggested_action="Check tool policy and agent permissions",
        )
    arguments = hook_ctx.payload.get("params", arguments)

    try:
        if cancel_checker is not None and cancel_checker():
            raise AgentCancelled("cancelled")
        # Capture the tool worker's tid so the cancel path can kill the
        # processes spawned by the currently running tool (产品决策: cancel
        # = 立刻停止，参考 pi killProcessTree / codex process_group)。
        worker_tid: list[int] = []

        # The registry (incl. the HITL gate) runs on the pool worker below,
        # but thread-affine approvers (ConfirmBridge) register their target
        # under the submitting thread — route the confirm by caller tid.
        caller_tid = threading.get_ident()
        confirm_as = getattr(hitl_approver, "confirm_as", None)
        if confirm_as is not None:
            def _hitl_approver(summary: str) -> bool:
                return confirm_as(caller_tid, summary)
        else:
            _hitl_approver = hitl_approver

        def _invoke_tracked(**kwargs):
            worker_tid.append(threading.get_ident())
            return tool_executor.invoke(**kwargs)

        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = executor.submit(
            _invoke_tracked,
            name=name,
            params=arguments,
            caller=caller,
            hitl_approver=_hitl_approver,
            tool_policy=tool_policy,
        )
        try:
            # Poll with periodic cancel checks — future.result() without a
            # timeout would make cancel signals invisible during tool
            # execution. The actual timeout cap is the registry's
            # ToolMeta.timeout_sec, enforced inside invoke().
            while True:
                if cancel_checker is not None and cancel_checker():
                    # Kill the in-flight tool's process tree immediately
                    # instead of waiting for it to finish.
                    if worker_tid:
                        from agentnexus.tools import process_tracker
                        process_tracker.kill_processes_for_thread(worker_tid[0])
                    future.cancel()  # no-op once running; pending only
                    raise AgentCancelled("cancelled")
                try:
                    result = future.result(timeout=0.2)
                    break
                except concurrent.futures.TimeoutError:
                    continue
        finally:
            # Do NOT wait for the tool thread: after a cancel-kill it exits
            # promptly, but non-killable tools (MCP remote calls) may keep
            # running until their own timeout — the agent must not block on
            # them. This replaces the old `with ThreadPoolExecutor` block,
            # which waited for the tool to complete even after cancel.
            executor.shutdown(wait=False)

        # ── after hook (observer) ──────────────────────────────
        fire_hook(HookType.AFTER_TOOL_CALL, {
            "name": name,
            "params": arguments,
            "result": result,
        })

        if isinstance(result, (dict, ToolError)):
            return result
        return str(result)
    except Exception as exc:
        # ── error hook (observer) ──────────────────────────────
        fire_hook(HookType.ON_TOOL_ERROR, {
            "name": name,
            "params": arguments,
            "error": exc,
        })
        _log_tool_error(name, exc)
        # LOW-02: Include message for safe domain exceptions, strip for generic ones
        for predicate, error_code, message_fn, recoverable, suggested_action in _ERROR_CLASSIFIERS:
            if predicate(exc):
                return ToolError(
                    error_code=error_code,
                    message=message_fn(name, exc),
                    recoverable=recoverable,
                    suggested_action=suggested_action,
                )
