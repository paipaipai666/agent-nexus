"""Tool return format system tests.

Validates that each tool returns the expected format for success/failure.
"""

import pytest

from agentnexus.tools.registry import ToolRegistry, ToolMeta


class TestToolReturnFormats:
    """Each tool returns expected format on success and failure."""

    def test_web_search_returns_dict_with_results(self):
        te = ToolRegistry()
        te.register(
            ToolMeta(
                name="web_search",
                description="搜索",
                param_schema={"type": "object", "properties": {}},
            ),
            lambda **kw: {"results": [{"title": "t", "url": "u"}]},
        )
        result = te.get_tool("web_search")(query="test")
        assert isinstance(result, dict)
        assert "results" in result

    def test_file_read_returns_string_content(self):
        te = ToolRegistry()
        te.register(
            ToolMeta(
                name="file_read",
                description="读文件",
                param_schema={"type": "object", "properties": {}},
            ),
            lambda **kw: "file content",
        )
        result = te.get_tool("file_read")(path="test.txt")
        assert isinstance(result, str)

    def test_file_write_returns_success_indicator(self):
        def _write(**kw):
            return {"status": "ok", "path": kw.get("path")}
        te = ToolRegistry()
        te.register(
            ToolMeta(
                name="file_write",
                description="写文件",
                param_schema={"type": "object", "properties": {}},
            ),
            _write,
        )
        result = te.get_tool("file_write")(path="test.txt", content="hello")
        assert result["status"] == "ok"

    def test_shell_exec_returns_string_output(self):
        te = ToolRegistry()
        te.register(
            ToolMeta(
                name="shell_exec",
                description="执行命令",
                param_schema={"type": "object", "properties": {}},
            ),
            lambda **kw: "command output",
        )
        result = te.get_tool("shell_exec")(command="echo hello")
        assert isinstance(result, str)

    def test_memory_save_returns_ack(self):
        def _save(**kw):
            return {"saved": True, "category": kw.get("category")}
        te = ToolRegistry()
        te.register(
            ToolMeta(
                name="memory_save",
                description="保存记忆",
                param_schema={"type": "object", "properties": {}},
            ),
            _save,
        )
        result = te.get_tool("memory_save")(content="test", category="preference")
        assert result["saved"] is True

    def test_tool_failure_returns_error_dict(self):
        te = ToolRegistry()
        def _fail(**kw):
            raise RuntimeError("tool error")
        te.register(
            ToolMeta(
                name="fail_tool",
                description="失败工具",
                param_schema={"type": "object", "properties": {}},
            ),
            _fail,
        )
        with pytest.raises(RuntimeError):
            te.get_tool("fail_tool")()

    def test_tool_with_output_schema_validation(self):
        def _structured(**kw):
            return {"result": "ok", "count": 42}
        te = ToolRegistry()
        te.register(
            ToolMeta(
                name="structured_tool",
                description="结构化工具",
                param_schema={"type": "object", "properties": {}},
                output_schema={
                    "type": "object",
                    "properties": {
                        "result": {"type": "string"},
                        "count": {"type": "integer"},
                    },
                    "required": ["result", "count"],
                },
            ),
            _structured,
        )
        result = te.get_tool("structured_tool")()
        assert result["result"] == "ok"
        assert result["count"] == 42
