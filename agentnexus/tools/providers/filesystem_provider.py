"""Filesystem tool provider — read and write local files."""

from __future__ import annotations

from agentnexus.tools.providers.base import ProviderSpec, ToolProviderContext
from agentnexus.tools.registry import ToolRegistry


class FilesystemToolProvider:
    def metadata(self) -> ProviderSpec:
        return ProviderSpec("filesystem", description="Read and write local files.")

    def register(self, executor: ToolRegistry, context: ToolProviderContext) -> None:
        from agentnexus.tools.file_ops import file_list, file_read, file_write

        before = set(executor.list_tools())
        if context.want("file_read"):
            executor.register_tool(
                "file_read",
                "读取文件内容，返回带行号的内容以及当前 version 指纹。参数: path(文件路径,必填), "
                "offset(起始行号,0起,默认0), limit(返回行数,默认最多1000)。"
                "[不适用] 搜索代码内容(用grep_search), 读取知识库文档(用kb_search)。",
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
                recoverable=True,
                concurrency_safe=True,
            )

        if context.want("file_list"):
            executor.register_tool(
                "file_list",
                "列出目录内容。参数: path(目录路径,默认当前目录), pattern(glob过滤,如 '*.py')。"
                "[不适用] 搜索文件内容(用grep_search)。",
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
                recoverable=True,
                concurrency_safe=True,
            )

        if context.want("file_write"):
            executor.register_tool(
                "file_write",
                "写入/创建文件。参数: path(文件路径), content(文件内容), "
                "mode(create=创建新文件/overwrite=覆盖已有文件/append=追加), "
                "expected_version(可选，来自 file_read 的 version，用于写前版本校验)。"
                "覆盖已有文件时需要确认。"
                "[不适用] 执行代码(用python_execute), 执行命令(用shell_exec)。",
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
                output_schema={
                    "type": "object",
                    "properties": {
                        "status": {"type": "string", "enum": ["ok", "error"]},
                        "message": {"type": "string"},
                        "path": {"type": "string"},
                        "mode": {"type": "string"},
                        "change_type": {"type": "string"},
                        "changed": {"type": "boolean"},
                        "version_before": {"type": "string"},
                        "version_after": {"type": "string"},
                        "stats": {
                            "type": "object",
                            "properties": {
                                "added_lines": {"type": "integer"},
                                "removed_lines": {"type": "integer"},
                                "hunks": {"type": "integer"},
                                "before_bytes": {"type": "integer"},
                                "after_bytes": {"type": "integer"},
                            },
                        },
                        "preview": {
                            "type": "object",
                            "properties": {
                                "format": {"type": "string"},
                                "text": {"type": "string"},
                                "truncated": {"type": "boolean"},
                                "max_lines": {"type": "integer"},
                                "max_chars": {"type": "integer"},
                                "shown_hunks": {"type": "integer"},
                                "total_hunks": {"type": "integer"},
                            },
                        },
                        "patch": {"type": ["string", "null"]},
                        "patch_ref": {"type": ["string", "null"]},
                        "is_binary": {"type": "boolean"},
                        "notes": {"type": "array", "items": {"type": "string"}},
                        "error_code": {"type": "string"},
                    },
                    "required": ["status", "message", "path", "mode", "changed"],
                },
            )
        context.mark_registered(executor, before)
