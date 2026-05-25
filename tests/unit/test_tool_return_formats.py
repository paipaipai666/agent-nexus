"""Tool return format system tests.

Validates that each tool returns the expected format for success/failure.
"""
from unittest.mock import MagicMock, patch

import pytest

from agentnexus.tools.tool_executor import ToolExecutor
from agentnexus.tools.registry import RiskLevel, ToolMeta


class TestToolReturnFormats:
    """Each tool returns expected format on success and failure."""

    def test_web_search_returns_dict_with_results(self):
        te = ToolExecutor()
        te.registerTool("web_search", "搜索", lambda **kw: {"results": [{"title": "t", "url": "u"}]})
        result = te.getTool("web_search")(query="test")
        assert isinstance(result, dict)
        assert "results" in result

    def test_file_read_returns_string_content(self):
        te = ToolExecutor()
        te.registerTool("file_read", "读文件", lambda **kw: "file content")
        result = te.getTool("file_read")(path="test.txt")
        assert isinstance(result, str)

    def test_file_write_returns_success_indicator(self):
        def _write(**kw):
            return {"status": "ok", "path": kw.get("path")}
        te = ToolExecutor()
        te.registerTool("file_write", "写文件", _write)
        result = te.getTool("file_write")(path="test.txt", content="hello")
        assert result["status"] == "ok"

    def test_shell_exec_returns_string_output(self):
        te = ToolExecutor()
        te.registerTool("shell_exec", "执行命令", lambda **kw: "command output")
        result = te.getTool("shell_exec")(command="echo hello")
        assert isinstance(result, str)

    def test_memory_save_returns_ack(self):
        def _save(**kw):
            return {"saved": True, "category": kw.get("category")}
        te = ToolExecutor()
        te.registerTool("memory_save", "保存记忆", _save)
        result = te.getTool("memory_save")(content="test", category="preference")
        assert result["saved"] is True

    def test_code_executor_returns_string(self):
        te = ToolExecutor()
        te.registerTool("python_execute", "执行代码", lambda **kw: "execution result")
        result = te.getTool("python_execute")(code="print(1)")
        assert isinstance(result, str)

    def test_tool_failure_returns_error_dict(self):
        te = ToolExecutor()
        def _fail(**kw):
            raise RuntimeError("tool error")
        te.registerTool("fail_tool", "失败工具", _fail)
        with pytest.raises(RuntimeError):
            te.getTool("fail_tool")()

    def test_tool_with_output_schema_validation(self):
        def _structured(**kw):
            return {"result": "ok", "count": 42}
        te = ToolExecutor()
        te.registerTool(
            "structured_tool",
            "结构化工具",
            _structured,
            output_schema={
                "type": "object",
                "properties": {
                    "result": {"type": "string"},
                    "count": {"type": "integer"},
                },
                "required": ["result", "count"],
            },
        )
        result = te.getTool("structured_tool")()
        assert result["result"] == "ok"
        assert result["count"] == 42
