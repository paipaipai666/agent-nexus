"""Memory tool provider — long-term memory search and save."""

from __future__ import annotations

from agentnexus.tools.providers.base import ProviderSpec, ToolProviderContext
from agentnexus.tools.registry import ToolRegistry


class MemoryToolProvider:
    def metadata(self) -> ProviderSpec:
        return ProviderSpec("memory", description="Long-term memory search and save tools.")

    def register(self, executor: ToolRegistry, context: ToolProviderContext) -> None:
        from agentnexus.tools.history_search import history_search
        from agentnexus.tools.memory_save import memory_project_status, memory_save
        from agentnexus.tools.memory_search import memory_search

        before = set(executor.list_tools())
        if context.want("history_search"):
            executor.register_tool(
                "history_search",
                "检索已折叠归档的早期对话原文（上下文中出现[历史索引目录]时，"
                "用于取回错误码/ID/路径/配置值/人名/日期等被折叠的具体细节）。"
                "[不适用] 检索长期记忆偏好(用memory_search), 搜索代码文件(用grep_search)。",
                history_search,
                param_schema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "max_results": {"type": "integer", "default": 5},
                    },
                    "required": ["query"],
                },
                risk_level="low",
                rate_limit_per_min=15,
                concurrency_safe=True,
            )
        if context.want("memory_search"):
            executor.register_tool(
                "memory_search",
                "检索长期记忆中的用户偏好、历史事实和结论，参数为搜索关键词。"
                "[不适用] 搜索代码文件(用grep_search), 搜索知识库文档(用kb_search)。",
                memory_search,
                param_schema={
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
                risk_level="low",
                rate_limit_per_min=10,
                concurrency_safe=True,
            )

        if context.want("memory_save"):
            executor.register_tool(
                "memory_save",
                "主动保存重要信息。用户个人信息/偏好用默认 scope=user 存入全局长期记忆；"
                "项目级知识(构建命令/约定/决策理由/踩坑)用 scope=project 写入当前项目 .agentnexus/ 纯文本文件。"
                "[不适用] 写入普通文件(用file_write)。",
                memory_save,
                param_schema={
                    "type": "object",
                    "properties": {
                        "content": {"type": "string"},
                        "category": {"type": "string", "default": "entity_fact"},
                        "importance": {"type": "number", "default": 0.7},
                        "scope": {"type": "string", "enum": ["user", "project"], "default": "user"},
                        "kind": {"type": "string", "enum": ["memo", "decision", "lesson", "log"], "default": "memo"},
                        "tags": {"type": "string", "default": ""},
                    },
                    "required": ["content"],
                },
                risk_level="low",
                rate_limit_per_min=10,
            )

        if context.want("memory_project_status"):
            executor.register_tool(
                "memory_project_status",
                "查看当前项目的项目级记忆(.agentnexus/ 目录)的索引和当前状态。"
                "[不适用] 搜索记忆内容(用memory_search), 写入记忆(用memory_save)。",
                memory_project_status,
                param_schema={"type": "object", "properties": {}},
                risk_level="low",
                rate_limit_per_min=10,
                concurrency_safe=True,
            )
        context.mark_registered(executor, before)
