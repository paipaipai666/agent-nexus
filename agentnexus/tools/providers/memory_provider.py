"""Memory tool provider — long-term memory search and save."""

from __future__ import annotations

from agentnexus.tools.providers.base import ProviderSpec, ToolProviderContext
from agentnexus.tools.registry import ToolRegistry


class MemoryToolProvider:
    def metadata(self) -> ProviderSpec:
        return ProviderSpec("memory", description="Long-term memory search and save tools.")

    def register(self, executor: ToolRegistry, context: ToolProviderContext) -> None:
        from agentnexus.tools.memory_save import memory_save
        from agentnexus.tools.memory_search import memory_search

        before = set(executor.list_tools())
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
                "主动保存重要信息到长期记忆。当用户明确分享个人信息(姓名/偏好/背景)或发现重要事实时使用。"
                "[不适用] 写入文件(用file_write)。",
                memory_save,
                param_schema={
                    "type": "object",
                    "properties": {
                        "content": {"type": "string"},
                        "category": {"type": "string", "default": "entity_fact"},
                        "importance": {"type": "number", "default": 0.7},
                    },
                    "required": ["content"],
                },
                risk_level="low",
                rate_limit_per_min=10,
            )
        context.mark_registered(executor, before)
