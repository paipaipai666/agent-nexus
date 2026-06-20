"""Todo tool provider — session-scoped task list management."""

from __future__ import annotations

from agentnexus.tools.providers.base import ProviderSpec, ToolProviderContext
from agentnexus.tools.registry import ToolRegistry


class TodoToolProvider:
    """Session-scoped todo list tools for agent task tracking."""

    def metadata(self) -> ProviderSpec:
        return ProviderSpec("todo", description="Task list management for complex task decomposition.")

    def register(self, executor: ToolRegistry, context: ToolProviderContext) -> None:
        todo_list = context.todo_list
        if todo_list is None:
            return

        before = set(executor.list_tools())

        if context.want("todo_add"):
            def _todo_add(description: str) -> str:
                item = todo_list.add(description)
                return f"Added todo #{item.id}: {item.description}"

            executor.register_tool(
                "todo_add",
                "将复杂任务分解为子任务并添加到清单。当判断任务需要2步以上完成时，必须先调用此工具。"
                "[不适用] 管理文件(用file_write)。",
                _todo_add,
                param_schema={
                    "type": "object",
                    "properties": {"description": {"type": "string"}},
                    "required": ["description"],
                },
                risk_level="low",
            )

        if context.want("todo_update"):
            def _todo_update(item_id: int, status: str) -> str:
                item = todo_list.update(item_id, status)
                return f"Updated todo #{item.id}: {item.status}"

            executor.register_tool(
                "todo_update",
                "更新任务状态。开始执行时标记 in_progress，完成后立即标记 done。不要等到所有任务都完成才更新。",
                _todo_update,
                param_schema={
                    "type": "object",
                    "properties": {
                        "item_id": {"type": "integer"},
                        "status": {"type": "string", "enum": ["pending", "in_progress", "done"]},
                    },
                    "required": ["item_id", "status"],
                },
                risk_level="low",
            )

        if context.want("todo_list"):
            def _todo_list() -> str:
                items = todo_list.list_items()
                if not items:
                    return "No todo items."
                lines = []
                for item in items:
                    marker = {"done": "[✓]", "in_progress": "[→]", "pending": "[·]"}.get(item.status, "[·]")
                    lines.append(f"#{item.id} {marker} {item.description}")
                return "\n".join(lines)

            executor.register_tool(
                "todo_list",
                "查看当前任务清单的完整状态。",
                _todo_list,
                param_schema={"type": "object", "properties": {}},
                risk_level="low",
                concurrency_safe=True,
            )

        context.mark_registered(executor, before)
