"""AgentNexus tools — shared registration and public API."""

from __future__ import annotations

from agentnexus.tools.tool_executor import ToolExecutor


def register_all_tools(executor: ToolExecutor, non_interactive: bool = False,
                       llm_client=None, include_tools: set[str] | None = None,
                       enable_subagent: bool = True):
    """Register all available tools on the given executor.

    Call this once per CLI/TUI entry point instead of duplicating
    individual registerTool() calls.

    Args:
        executor: ToolExecutor instance to register tools on.
        non_interactive: If True, skip HITL confirmation for high-risk tools.
        llm_client: Optional parent LLM used to clone subagent settings.
        include_tools: Optional allowlist of tool names to register.
        enable_subagent: Whether to register the subagent delegation tool.
    """
    from agentnexus.tools.code_executor import python_execute
    from agentnexus.tools.file_ops import file_list, file_read, file_write
    from agentnexus.tools.grep_search import grep_search
    from agentnexus.tools.kb_search import kb_search
    from agentnexus.tools.memory_save import memory_save
    from agentnexus.tools.memory_search import memory_search
    from agentnexus.tools.shell import get_os_info, shell_exec
    from agentnexus.tools.web_search import web_search

    os_info = get_os_info()

    def want(name: str) -> bool:
        return include_tools is None or name in include_tools

    # ── Low-risk: read-only ──────────────────────────────────────
    if want("memory_search"):
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

    if want("memory_save"):
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

    if want("grep_search"):
        executor.registerTool(
            "grep_search",
            "使用 ripgrep 在项目中搜索文本。默认字面量匹配（非正则），"
            "直接搜函数名、类名、导入、错误消息等即可，无需转义。"
            "参数: pattern(搜索文本,必填), "
            "path(搜索目录,默认当前目录), "
            "glob(文件过滤,如 '*.py'), "
            "max_results(最大结果数,1-50,默认10), "
            "literal(字面量匹配,默认true; 设为false启用正则)",
            grep_search,
            param_schema={
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "搜索文本（默认字面量匹配）"},
                    "path": {"type": "string", "description": "搜索目录", "default": "."},
                    "glob": {"type": "string", "description": "文件过滤模式 (如 *.py, *.yaml)", "default": "*"},
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

    if want("web_search"):
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
                    "max_results": {"type": "integer",
                                    "description": "返回结果数量 (1-20)",
                                    "default": 5},
                    "search_depth": {"type": "string", "enum": ["basic", "advanced"],
                                     "description": "搜索深度，basic=快速，advanced=深度"},
                    "time_range": {"type": "string", "enum": ["day", "week", "month", "year"],
                                   "description": "时间范围过滤"},
                    "topic": {"type": "string", "enum": ["general", "news"],
                              "description": "搜索话题类型", "default": "general"},
                    "include_answer": {"type": "boolean",
                                       "description": "是否包含 Tavily 生成的直接答案摘要",
                                       "default": False},
                },
                "required": ["query"],
            },
            risk_level="low",
            rate_limit_per_min=10,
        )

    if want("kb_search"):
        executor.registerTool(
            "kb_search",
            "检索结构化知识库，返回带来源与分数的结果。"
            "参数: query(搜索词,必填), "
            "namespace(知识库命名空间,默认default), "
            "top_k(返回条数,默认5)",
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
                },
                "required": ["query"],
            },
            risk_level="low",
            rate_limit_per_min=20,
        )

    if want("file_read"):
        executor.registerTool(
            "file_read",
            "读取文件内容，返回带行号的内容。参数: path(文件路径,必填), "
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

    if want("file_list"):
        executor.registerTool(
            "file_list",
            "列出目录内容。参数: path(目录路径,默认当前目录), "
            "pattern(glob过滤,如 '*.py')",
            file_list,
            param_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "目录路径（相对于工作目录）", "default": "."},
                    "pattern": {"type": "string", "description": "glob 过滤模式 (如 *.py, test_*)", "default": None},
                },
                "required": [],
            },
            risk_level="low",
            rate_limit_per_min=20,
        )

    # ── Medium-risk: write ops ───────────────────────────────────
    if want("file_write"):
        executor.registerTool(
            "file_write",
            "写入/创建文件。参数: path(文件路径), content(文件内容), "
            "mode(create=创建新文件/overwrite=覆盖已有文件/append=追加)。"
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
                },
                "required": ["path", "content"],
            },
            risk_level="medium",
            require_hitl=not non_interactive,
            timeout_sec=10,
            rate_limit_per_min=20,
        )

    # ── High-risk: code & shell execution ────────────────────────
    if want("python_execute"):
        executor.registerTool(
            "python_execute",
            "在安全沙箱中执行Python代码，参数为代码字符串",
            python_execute,
            param_schema={
                "type": "object",
                "properties": {"code": {"type": "string"}},
                "required": ["code"],
            },
            risk_level="high",
            require_hitl=not non_interactive,
            timeout_sec=60,
        )

    if want("shell_exec"):
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
            require_hitl=not non_interactive,
            timeout_sec=60,
        )

    if enable_subagent and want("subagent_run"):
        from agentnexus.tools.subagent import make_subagent_run

        executor.registerTool(
            "subagent_run",
            "将一个明确、可独立完成、输入充分的子任务委派给受限子代理执行。适合阅读、检索、归纳等局部任务；返回结构化结果供父代理继续综合。参数: task(必填), role(可选), allowed_tools(可选白名单), max_steps(默认4)",
            make_subagent_run(parent_llm=llm_client, non_interactive=non_interactive),
            param_schema={
                "type": "object",
                "properties": {
                    "task": {"type": "string", "description": "子任务描述"},
                    "role": {
                        "type": "string",
                        "enum": ["general", "researcher", "reader", "analyst"],
                        "default": "general",
                    },
                    "allowed_tools": {
                        "type": "array",
                        "items": {"type": "string"},
                        "default": [],
                    },
                    "max_steps": {"type": "integer", "default": 4},
                },
                "required": ["task"],
            },
            risk_level="low",
            rate_limit_per_min=10,
        )
