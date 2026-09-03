"""Subagent tool provider — delegation tool for child ReAct agents."""

from __future__ import annotations

from agentnexus.tools.providers.base import ProviderSpec, ToolProviderContext
from agentnexus.tools.registry import ToolRegistry


class SubagentToolProvider:
    def metadata(self) -> ProviderSpec:
        return ProviderSpec("subagent", description="Delegation tool for controlled child ReAct agents.")

    def register(self, executor: ToolRegistry, context: ToolProviderContext) -> None:
        if not context.enable_subagent or not context.want("subagent_run"):
            return

        from agentnexus.tools.subagent import make_subagent_run

        before = set(executor.list_tools())
        executor.register_tool(
            "subagent_run",
            (
                "将一个明确、可独立完成、输入充分的子任务委派给子代理执行。默认是 Explorer"
                "（阅读、检索、归纳）；使用 executor 时可在受控条件下运行 Python 片段验证结果。"
                "优先通过 task 和 allowed_tools 约束子代理范围。旧 role 值"
                " reader/researcher/analyst 会映射到 explorer。返回结构化结果供父代理继续综合。"
                "参数: task(必填), role(兼容字段,可选), allowed_tools(可选白名单), max_steps(默认4)。"
                "[不适用] 简单单步任务(直接调用具体工具), 需要实时交互的任务。"
            ),
            make_subagent_run(
                parent_llm=context.llm_client,
                non_interactive=context.non_interactive,
                subagent_confirm=context.subagent_confirm,
                mcp_manager=context.mcp_manager,
                cancel_bridge=executor.cancel_bridge,
            ),
            param_schema={
                "type": "object",
                "properties": {
                    "task": {"type": "string", "description": "子任务描述"},
                    "role": {
                        "type": "string",
                        "enum": ["explorer", "executor", "general", "researcher", "reader", "analyst"],
                        "default": "explorer",
                    },
                    "allowed_tools": {"type": "array", "items": {"type": "string"}, "default": []},
                    "max_steps": {"type": "integer", "default": 4},
                },
                "required": ["task"],
            },
            risk_level="low",
            rate_limit_per_min=10,
            # Subagents run on the session-scoped "subagent" lane pool whose
            # size (settings.subagent_max_concurrent) caps their parallelism;
            # child agents share nothing mutable with the parent run.
            concurrency_safe=True,
            lane="subagent",
        )
        context.mark_registered(executor, before)
