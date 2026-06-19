"""Execution tool provider — Python and shell execution."""

from __future__ import annotations

from agentnexus.tools.providers.base import ProviderSpec, ToolProviderContext
from agentnexus.tools.registry import ToolRegistry


class ExecutionToolProvider:
    def metadata(self) -> ProviderSpec:
        return ProviderSpec(
            "execution",
            description="High-risk Python and shell execution tools.",
            exposed_agents=("react_agent", "subagent_executor"),
        )

    def register(self, executor: ToolRegistry, context: ToolProviderContext) -> None:
        from agentnexus.tools.code_executor import python_execute
        from agentnexus.tools.shell import get_os_info, shell_exec

        before = set(executor.list_tools())
        os_info = get_os_info()
        if context.want("python_execute"):
            executor.register_tool(
                "python_execute",
                "在安全沙箱中执行Python代码，参数为代码字符串",
                python_execute,
                param_schema={
                    "type": "object",
                    "properties": {"code": {"type": "string"}},
                    "required": ["code"],
                },
                allowed_agents=["react_agent", "subagent_executor"],
                risk_level="high",
                require_hitl=not context.non_interactive,
                timeout_sec=60,
            )

        if context.want("shell_exec"):
            executor.register_tool(
                "shell_exec",
                f"执行控制台命令（当前系统: {os_info}）。参数: command(命令字符串,必填), "
                "cwd(工作目录,可选,默认项目根目录), timeout(超时秒数,默认30)。"
                "[!] 此工具需要用户确认才能执行，同时受安全黑名单保护",
                shell_exec,
                param_schema={
                    "type": "object",
                    "properties": {
                        "command": {"type": "string", "description": "要执行的 shell 命令"},
                        "cwd": {"type": "string", "description": "工作目录（相对于项目根目录）", "default": None},
                        "timeout": {"type": "integer", "description": "超时秒数 (默认 30)", "default": 30},
                    },
                    "required": ["command"],
                },
                risk_level="high",
                require_hitl=not context.non_interactive,
                timeout_sec=60,
            )
        context.mark_registered(executor, before)
