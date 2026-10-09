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
        worker_tid: list[int] = []

        caller_tid = threading.get_ident()
        confirm_as = getattr(hitl_approver, "confirm_as", None)
        hitl_preapproved = [False]
        if hitl_approver is None and confirm_as is None:
            _hitl_approver = None
        else:
            def _hitl_approver(summary: str) -> bool:
                if hitl_preapproved[0]:
                    hitl_preapproved[0] = False
                    return True
                if confirm_as is not None:
                    return confirm_as(caller_tid, summary)
                return hitl_approver(summary)

        if _hitl_approver is not None:
            from agentnexus.tools.file_ops import path_guard_violation
            violation = path_guard_violation(name, arguments)
            if violation is not None:
                summary = (
                    "路径越界请求放行\n"
                    f"工具: {name}\n"
                    f"请求路径: {violation.path}\n"
                    f"解析为: {violation.resolved}\n"
                    f"允许的根目录: {', '.join(violation.roots)}\n\n"
                    "批准 = 本会话（当前项目）放行该路径；\n"
                    "永久放行请将该路径加入 config.yaml 的 allowed_paths。"
                )
                if _hitl_approver(summary):
                    from agentnexus.tools.workspace import approve_extra_path
                    approve_extra_path(violation.resolved)
                    hitl_preapproved[0] = True
                else:
                    return ToolError(
                        error_code=ToolErrorCode.HITL_BLOCKED,
                        message=f"用户拒绝了越界路径访问: {violation.path}",
                        recoverable=False,
                        suggested_action="改用允许目录内的路径，或请用户加入 allowed_paths",
                    )

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
            while True:
                if cancel_checker is not None and cancel_checker():
                    if worker_tid:
                        from agentnexus.tools import process_tracker
                        process_tracker.kill_processes_for_thread(worker_tid[0])
                    future.cancel()
                    raise AgentCancelled("cancelled")
                try:
                    result = future.result(timeout=0.2)
                    break
                except concurrent.futures.TimeoutError:
                    continue
        finally:
            executor.shutdown(wait=False)

        fire_hook(HookType.AFTER_TOOL_CALL, {
            "name": name,
            "params": arguments,
            "result": result,
        })

        if isinstance(result, (dict, ToolError)):
            return result
        return str(result)
    except Exception as exc:
        fire_hook(HookType.ON_TOOL_ERROR, {
            "name": name,
            "params": arguments,
            "error": exc,
        })
        _log_tool_error(name, exc)
        for predicate, error_code, message_fn, recoverable, suggested_action in _ERROR_CLASSIFIERS:
            if predicate(exc):
                return ToolError(
                    error_code=error_code,
                    message=message_fn(name, exc),
                    recoverable=recoverable,
                    suggested_action=suggested_action,
                )
