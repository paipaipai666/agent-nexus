"""Plan-mode tool provider — the exit_plan_mode review tool."""

from __future__ import annotations

from agentnexus.tools.providers.base import ProviderSpec, ToolProviderContext
from agentnexus.tools.registry import ToolRegistry


class PlanModeToolProvider:
    def metadata(self) -> ProviderSpec:
        return ProviderSpec("plan_mode", description="计划模式退出申请")

    def register(self, executor: ToolRegistry, context: ToolProviderContext) -> None:
        before = set(executor.list_tools())

        if context.want("exit_plan_mode"):
            def _exit_plan_mode(plan: str) -> str:
                # 真实逻辑由 ReActAgent._execute_tool 的计划模式门禁拦截；
                # 走到这里说明不在计划模式（或其它绕过 agent 层的调用路径）。
                return "[exit_plan_mode] 当前不在计划模式，未提交任何计划供审批。"

            executor.register_tool(
                "exit_plan_mode",
                "仅在计划模式下使用：提交最终计划文档并申请退出计划模式。"
                "参数: plan(必填，完整的 Markdown 计划文档)，将完整展示给用户审批。"
                "批准后计划模式关闭，写入工具恢复可用。"
                "[不适用] 非计划模式下调用（直接正常干活即可）。",
                _exit_plan_mode,
                param_schema={
                    "type": "object",
                    "properties": {
                        "plan": {"type": "string", "description": "完整计划文档 (Markdown)"},
                    },
                    "required": ["plan"],
                },
                risk_level="low",
                # 保持默认 concurrency_safe=False：必须落 dispatcher 顺序组、
                # 在 run 线程执行（桌面 HITL 确认按 run 线程路由）。
            )
        context.mark_registered(executor, before)
