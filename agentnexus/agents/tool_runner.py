"""Tool execution coordination helpers for ReActAgent."""

from __future__ import annotations

from typing import Any, Callable


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
    try:
        if cancel_checker is not None and cancel_checker():
            raise RuntimeError("cancelled")
        return str(tool_executor.registry.invoke(
            name=name,
            params=arguments,
            caller=caller,
            hitl_approver=hitl_approver,
            tool_policy=tool_policy,
        ))
    except Exception as exc:
        return f"错误: 工具 '{name}' 执行失败: {exc}"
