"""CodeGraph tool provider — code knowledge graph search and query."""

from __future__ import annotations

from agentnexus.tools.providers.base import ProviderSpec, ToolProviderContext
from agentnexus.tools.registry import ToolRegistry


class CodeGraphToolProvider:
    """Code knowledge graph search and query tools."""

    def metadata(self) -> ProviderSpec:
        return ProviderSpec("codegraph", description="Code knowledge graph search and query tools.")

    def register(self, executor: ToolRegistry, context: ToolProviderContext) -> None:
        from agentnexus.codegraph.queries import (
            codegraph_context,
            codegraph_relations,
            codegraph_search,
        )

        before = set(executor.list_tools())

        if context.want("codegraph_search"):
            executor.register_tool(
                "codegraph_search",
                "语义搜索代码实体。参数: query(搜索词,必填), kind(节点类型过滤,可选), limit(返回条数,默认10)。"
                "[不适用] 搜索代码文本内容(用grep_search), 搜索知识库(用kb_search)。",
                codegraph_search,
                param_schema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "搜索关键词（必填）"},
                        "kind": {
                            "type": "string",
                            "enum": ["function", "method", "class", "file"],
                            "description": "节点类型过滤",
                            "default": None,
                        },
                        "limit": {
                            "type": "integer",
                            "description": "返回结果数量",
                            "default": 10,
                        },
                    },
                    "required": ["query"],
                },
                risk_level="low",
                rate_limit_per_min=20,
                concurrency_safe=True,
            )

        if context.want("codegraph_relations"):
            executor.register_tool(
                "codegraph_relations",
                "查询代码实体的关系。参数: symbol(实体名,必填), relation(关系类型:callers/callees/inherits/imports)。"
                "[不适用] 搜索代码文本(用grep_search)。",
                codegraph_relations,
                param_schema={
                    "type": "object",
                    "properties": {
                        "symbol": {"type": "string", "description": "实体名称（必填）"},
                        "relation": {
                            "type": "string",
                            "enum": ["callers", "callees", "inherits", "imports"],
                            "description": "关系类型",
                        },
                    },
                    "required": ["symbol", "relation"],
                },
                risk_level="low",
                rate_limit_per_min=20,
                concurrency_safe=True,
            )

        if context.want("codegraph_context"):
            executor.register_tool(
                "codegraph_context",
                "获取代码实体的完整上下文。参数: symbol(实体名,必填)。"
                "[不适用] 搜索代码文本(用grep_search)。",
                codegraph_context,
                param_schema={
                    "type": "object",
                    "properties": {
                        "symbol": {"type": "string", "description": "实体名称（必填）"},
                    },
                    "required": ["symbol"],
                },
                risk_level="low",
                rate_limit_per_min=20,
                concurrency_safe=True,
            )

        context.mark_registered(executor, before)
