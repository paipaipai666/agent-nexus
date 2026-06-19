"""Search tool provider — grep, web search, knowledge-base search, and web fetch."""

from __future__ import annotations

from agentnexus.tools.providers.base import ProviderSpec, ToolProviderContext
from agentnexus.tools.registry import ToolRegistry


class SearchToolProvider:
    def metadata(self) -> ProviderSpec:
        return ProviderSpec("search", description="Project grep, web search, and knowledge-base search tools.")

    def register(self, executor: ToolRegistry, context: ToolProviderContext) -> None:
        from agentnexus.tools.grep_search import grep_search
        from agentnexus.tools.kb_search import kb_search
        from agentnexus.tools.web_fetch import web_fetch
        from agentnexus.tools.web_search import web_search

        before = set(executor.list_tools())
        if context.want("grep_search"):
            executor.register_tool(
                "grep_search",
                "使用 ripgrep 在项目中搜索文本。默认字面量匹配（非正则），"
                "直接搜函数名、类名、导入、错误消息等即可，无需转义。"
                "参数: pattern(搜索文本,必填), "
                "path(搜索目录,默认当前目录), "
                "glob(文件过滤,如 '*.py' 或 '**/*.py', 默认 '*'), "
                "max_results(最大结果数,1-50,默认10), "
                "literal(字面量匹配,默认true; 设为false启用正则)",
                grep_search,
                param_schema={
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string", "description": "搜索文本（默认字面量匹配）"},
                        "path": {"type": "string", "description": "搜索目录", "default": "."},
                        "glob": {
                            "type": "string",
                            "description": (
                                "文件过滤模式。支持: *.py (所有py文件), **/*.py (同*.py), "
                                "test_* (所有test_开头的文件), **/test_* (同test_*), "
                                "[abc].py (字符类), [!abc].py (排除字符类)"
                            ),
                            "default": "*",
                        },
                        "max_results": {"type": "integer", "description": "最大结果数 (1-50)", "default": 10},
                        "literal": {
                            "type": "boolean",
                            "description": "字面量匹配 (默认true)。设为false启用正则",
                            "default": True,
                        },
                    },
                    "required": ["pattern"],
                },
                risk_level="low",
                rate_limit_per_min=20,
                recoverable=True,
            )

        if context.want("web_search"):
            executor.register_tool(
                "web_search",
                "搜索互联网获取实时信息。参数: query(搜索词,必填), "
                "max_results(返回条数,1-20,默认5), "
                "search_depth(搜索深度:basic/advanced,默认自动), "
                "time_range(时间范围:day/week/month/year,默认不限), "
                "topic(话题:general/news,默认general), "
                "include_answer(是否返回直接摘要,默认false), "
                "include_domains(限定搜索域名列表,如['arxiv.org']), "
                "exclude_domains(排除域名列表,如['reddit.com'])",
                web_search,
                param_schema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "搜索关键词（必填）"},
                        "max_results": {
                            "type": "integer",
                            "description": "返回结果数量 (1-20)",
                            "default": 5,
                        },
                        "search_depth": {
                            "type": "string",
                            "enum": ["basic", "advanced"],
                            "description": "搜索深度，basic=快速，advanced=深度",
                        },
                        "time_range": {
                            "type": "string",
                            "enum": ["day", "week", "month", "year"],
                            "description": "时间范围过滤",
                        },
                        "topic": {
                            "type": "string",
                            "enum": ["general", "news"],
                            "description": "搜索话题类型",
                            "default": "general",
                        },
                        "include_answer": {
                            "type": "boolean",
                            "description": "是否包含 Tavily 生成的直接答案摘要",
                            "default": False,
                        },
                        "include_domains": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "限定搜索的域名列表，如 ['arxiv.org', 'github.com']",
                        },
                        "exclude_domains": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "排除的域名列表，如 ['reddit.com', 'pinterest.com']",
                        },
                    },
                    "required": ["query"],
                },
                risk_level="low",
                rate_limit_per_min=10,
                recoverable=True,
            )

        if context.want("kb_search"):
            executor.register_tool(
                "kb_search",
                "检索结构化知识库，返回带来源与分数的结果。"
                "参数: query(搜索词,必填), "
                "namespace(知识库命名空间,默认default), "
                "top_k(返回条数,默认5), "
                "view(section=去重章节视图/chunk=原始块视图), "
                "source/format/section/page/block_type/has_code/has_list/heading_depth(可选过滤)",
                kb_search,
                param_schema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "搜索关键词（必填）"},
                        "namespace": {
                            "type": "string",
                            "description": "知识库命名空间",
                            "default": "default",
                        },
                        "top_k": {"type": "integer", "description": "返回结果数量", "default": 5},
                        "view": {
                            "type": "string",
                            "enum": ["section", "chunk"],
                            "description": "结果视图: section=章节聚合, chunk=原始块",
                            "default": "section",
                        },
                        "source": {"type": "string", "description": "按 source_uri 过滤", "default": ""},
                        "file_format": {
                            "type": "string",
                            "description": "按文档格式过滤，如 markdown/pdf/text",
                            "default": "",
                        },
                        "section_title": {"type": "string", "description": "按章节标题过滤", "default": ""},
                        "page_number": {"type": "integer", "description": "按页码过滤"},
                        "block_type": {
                            "type": "string",
                            "enum": ["paragraph", "list", "heading", "code"],
                            "description": "按块类型过滤",
                            "default": "",
                        },
                        "has_code": {"type": "boolean", "description": "过滤是否包含代码块"},
                        "has_list": {"type": "boolean", "description": "过滤是否包含列表块"},
                        "heading_depth": {"type": "integer", "description": "按标题层级过滤"},
                    },
                    "required": ["query"],
                },
                risk_level="low",
                rate_limit_per_min=20,
                recoverable=True,
            )

        if context.want("web_fetch"):
            executor.register_tool(
                "web_fetch",
                "抓取指定URL的完整网页内容并返回正文。"
                "参数: urls(要抓取的URL,必填,单个字符串或URL数组), "
                "extract_depth(提取深度:basic/advanced,默认basic), "
                "format(输出格式:markdown/text,默认markdown)",
                web_fetch,
                param_schema={
                    "type": "object",
                    "properties": {
                        "urls": {
                            "description": "要抓取的URL，单个字符串或URL数组",
                        },
                        "extract_depth": {
                            "type": "string",
                            "enum": ["basic", "advanced"],
                            "description": "提取深度，basic=快速，advanced=深度",
                        },
                        "format": {
                            "type": "string",
                            "enum": ["markdown", "text"],
                            "description": "输出格式",
                            "default": "markdown",
                        },
                    },
                    "required": ["urls"],
                },
                risk_level="low",
                rate_limit_per_min=5,
                recoverable=True,
            )

        context.mark_registered(executor, before)
