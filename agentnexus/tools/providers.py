"""Tool provider abstractions and built-in provider registrations."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from agentnexus.tools.tool_executor import ToolExecutor


@dataclass(frozen=True)
class ProviderSpec:
    """Metadata describing a group of tools that can be registered together."""

    name: str
    version: str = "1.0"
    default_enabled: bool = True
    required_config: tuple[str, ...] = ()
    exposed_agents: tuple[str, ...] = ("*",)
    description: str = ""


@dataclass
class ToolProviderContext:
    """Runtime inputs shared by tool providers during registration."""

    non_interactive: bool = False
    llm_client: Any = None
    include_tools: set[str] | None = None
    enable_subagent: bool = True
    subagent_confirm: Any = None
    mcp_manager: Any = None
    runtime: Any = None
    extension_context: Any = None
    source_type: str = "builtin"
    source_id: str = ""
    generation: int = 0
    registered_tools: list[str] = field(default_factory=list)

    def want(self, name: str) -> bool:
        return self.include_tools is None or name in self.include_tools

    def mark_registered(self, executor: ToolExecutor, before: set[str]) -> None:
        after = set(executor.registry.list_tools())
        added = sorted(after - before)
        source_id = self.source_id or self.source_type
        for name in added:
            entry = executor.registry._tools.get(name)
            if entry is None:
                continue
            meta, func = entry
            if meta.source_type != "unknown" or meta.source_id != "unknown":
                continue
            meta.source_type = self.source_type
            meta.source_id = source_id
            meta.generation = self.generation
            executor.registry._tools[name] = (meta, func)
        self.registered_tools.extend(added)

    def for_provider(self, provider_name: str, source_type: str | None = None, generation: int | None = None):
        return ToolProviderContext(
            non_interactive=self.non_interactive,
            llm_client=self.llm_client,
            include_tools=self.include_tools,
            enable_subagent=self.enable_subagent,
            subagent_confirm=self.subagent_confirm,
            mcp_manager=self.mcp_manager,
            runtime=self.runtime,
            extension_context=self.extension_context,
            source_type=source_type or self.source_type,
            source_id=provider_name,
            generation=self.generation if generation is None else generation,
            registered_tools=self.registered_tools,
        )


class ToolProvider(Protocol):
    """Register one cohesive group of tools on a ToolExecutor."""

    def metadata(self) -> ProviderSpec:
        ...

    def register(self, executor: ToolExecutor, context: ToolProviderContext) -> None:
        ...


class MemoryToolProvider:
    def metadata(self) -> ProviderSpec:
        return ProviderSpec("memory", description="Long-term memory search and save tools.")

    def register(self, executor: ToolExecutor, context: ToolProviderContext) -> None:
        from agentnexus.tools.memory_save import memory_save
        from agentnexus.tools.memory_search import memory_search

        before = set(executor.registry.list_tools())
        if context.want("memory_search"):
            executor.registerTool(
                "memory_search",
                "检索长期记忆中的用户偏好、历史事实和结论，参数为搜索关键词",
                memory_search,
                param_schema={
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
                risk_level="low",
                rate_limit_per_min=10,
            )

        if context.want("memory_save"):
            executor.registerTool(
                "memory_save",
                "主动保存重要信息到长期记忆。当用户明确分享个人信息(姓名/偏好/背景)或发现重要事实时使用",
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


class SearchToolProvider:
    def metadata(self) -> ProviderSpec:
        return ProviderSpec("search", description="Project grep, web search, and knowledge-base search tools.")

    def register(self, executor: ToolExecutor, context: ToolProviderContext) -> None:
        from agentnexus.tools.grep_search import grep_search
        from agentnexus.tools.kb_search import kb_search
        from agentnexus.tools.web_search import web_search

        before = set(executor.registry.list_tools())
        if context.want("grep_search"):
            executor.registerTool(
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
            )

        if context.want("web_search"):
            executor.registerTool(
                "web_search",
                "搜索互联网获取实时信息。参数: query(搜索词,必填), "
                "max_results(返回条数,1-20,默认5), "
                "search_depth(搜索深度:basic/advanced,默认自动), "
                "time_range(时间范围:day/week/month/year,默认不限), "
                "topic(话题:general/news,默认general), "
                "include_answer(是否返回直接摘要,默认false)",
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
                    },
                    "required": ["query"],
                },
                risk_level="low",
                rate_limit_per_min=10,
            )

        if context.want("kb_search"):
            executor.registerTool(
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
            )
        context.mark_registered(executor, before)


class FilesystemToolProvider:
    def metadata(self) -> ProviderSpec:
        return ProviderSpec("filesystem", description="Read and write local files.")

    def register(self, executor: ToolExecutor, context: ToolProviderContext) -> None:
        from agentnexus.tools.file_ops import file_list, file_read, file_write

        before = set(executor.registry.list_tools())
        if context.want("file_read"):
            executor.registerTool(
                "file_read",
                "读取文件内容，返回带行号的内容以及当前 version 指纹。参数: path(文件路径,必填), "
                "offset(起始行号,0起,默认0), limit(返回行数,默认最多1000)",
                file_read,
                param_schema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "文件路径（相对于工作目录）"},
                        "offset": {"type": "integer", "description": "起始行号 (0-indexed)", "default": 0},
                        "limit": {"type": "integer", "description": "返回行数上限", "default": 500},
                    },
                    "required": ["path"],
                },
                risk_level="low",
                rate_limit_per_min=30,
            )

        if context.want("file_list"):
            executor.registerTool(
                "file_list",
                "列出目录内容。参数: path(目录路径,默认当前目录), pattern(glob过滤,如 '*.py')",
                file_list,
                param_schema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "目录路径（相对于工作目录）", "default": "."},
                        "pattern": {
                            "type": "string",
                            "description": "glob 过滤模式 (如 *.py, test_*)",
                            "default": None,
                        },
                    },
                    "required": [],
                },
                risk_level="low",
                rate_limit_per_min=20,
            )

        if context.want("file_write"):
            executor.registerTool(
                "file_write",
                "写入/创建文件。参数: path(文件路径), content(文件内容), "
                "mode(create=创建新文件/overwrite=覆盖已有文件/append=追加), "
                "expected_version(可选，来自 file_read 的 version，用于写前版本校验)。"
                "覆盖已有文件时需要确认",
                file_write,
                param_schema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "文件路径（相对于工作目录）"},
                        "content": {"type": "string", "description": "要写入的文件内容"},
                        "mode": {
                            "type": "string",
                            "enum": ["create", "overwrite", "append"],
                            "description": "写入模式: create=新建, overwrite=覆盖, append=追加",
                            "default": "create",
                        },
                        "expected_version": {
                            "type": "string",
                            "description": "可选文件版本指纹，来自 file_read 的 version，用于写前冲突检测",
                            "default": None,
                        },
                    },
                    "required": ["path", "content"],
                },
                risk_level="medium",
                require_hitl=not context.non_interactive,
                timeout_sec=10,
                rate_limit_per_min=20,
            )
        context.mark_registered(executor, before)


class ExecutionToolProvider:
    def metadata(self) -> ProviderSpec:
        return ProviderSpec(
            "execution",
            description="High-risk Python and shell execution tools.",
            exposed_agents=("react_agent", "subagent_executor"),
        )

    def register(self, executor: ToolExecutor, context: ToolProviderContext) -> None:
        from agentnexus.tools.code_executor import python_execute
        from agentnexus.tools.shell import get_os_info, shell_exec

        before = set(executor.registry.list_tools())
        os_info = get_os_info()
        if context.want("python_execute"):
            executor.registerTool(
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
            executor.registerTool(
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


class McpBridgeToolProvider:
    def metadata(self) -> ProviderSpec:
        return ProviderSpec("mcp-bridge", description="Bridge tools discovered from configured MCP servers.")

    def register(self, executor: ToolExecutor, context: ToolProviderContext) -> None:
        if context.mcp_manager is None:
            return
        before = set(executor.registry.list_tools())
        context.mcp_manager.register_tools(executor, include_tools=context.include_tools)
        context.mark_registered(executor, before)


class SubagentToolProvider:
    def metadata(self) -> ProviderSpec:
        return ProviderSpec("subagent", description="Delegation tool for controlled child ReAct agents.")

    def register(self, executor: ToolExecutor, context: ToolProviderContext) -> None:
        if not context.enable_subagent or not context.want("subagent_run"):
            return

        from agentnexus.tools.subagent import make_subagent_run

        before = set(executor.registry.list_tools())
        executor.registerTool(
            "subagent_run",
            (
                "将一个明确、可独立完成、输入充分的子任务委派给子代理执行。默认是 Explorer"
                "（阅读、检索、归纳）；使用 executor 时可在受控条件下运行 Python 片段验证结果。"
                "优先通过 task 和 allowed_tools 约束子代理范围。旧 role 值"
                " reader/researcher/analyst 会映射到 explorer。返回结构化结果供父代理继续综合。"
                "参数: task(必填), role(兼容字段,可选), allowed_tools(可选白名单), max_steps(默认4)"
            ),
            make_subagent_run(
                parent_llm=context.llm_client,
                non_interactive=context.non_interactive,
                subagent_confirm=context.subagent_confirm,
                mcp_manager=context.mcp_manager,
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
        )
        context.mark_registered(executor, before)


def default_tool_providers() -> list[ToolProvider]:
    """Return the built-in provider order used by legacy registration."""

    return [
        MemoryToolProvider(),
        SearchToolProvider(),
        FilesystemToolProvider(),
        ExecutionToolProvider(),
        McpBridgeToolProvider(),
        SubagentToolProvider(),
    ]


def register_tool_providers(
    executor: ToolExecutor,
    providers: list[ToolProvider] | None = None,
    context: ToolProviderContext | None = None,
) -> list[str]:
    """Register all enabled providers and return names of tools added."""

    ctx = context or ToolProviderContext()
    for provider in providers or default_tool_providers():
        provider.register(executor, ctx.for_provider(provider.metadata().name))
    return ctx.registered_tools
