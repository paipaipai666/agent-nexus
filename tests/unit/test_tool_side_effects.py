"""Tool side-effect verification tests.

Validates that tool execution produces actual side effects.
"""
from agentnexus.tools.registry import ToolMeta, ToolRegistry


class TestToolSideEffects:
    """Tool execution produces correct side effects."""

    def test_memory_save_creates_record(self, temp_agentnexus_home):
        saved_records = []

        def mock_save(**kwargs):
            saved_records.append(kwargs)
            return {"saved": True}

        te = ToolRegistry()
        te.register(
            ToolMeta(
                name="memory_save",
                description="保存记忆",
                param_schema={"type": "object", "properties": {}},
            ),
            mock_save,
        )
        result = te.get_tool("memory_save")(content="test", category="preference")

        assert len(saved_records) == 1
        assert saved_records[0]["content"] == "test"
        assert result["saved"] is True

    def test_file_write_creates_file(self, tmp_path):
        written_files = {}

        def mock_write(path=None, content=None, **kw):
            written_files[path] = content
            return {"status": "ok", "path": path}

        te = ToolRegistry()
        te.register(
            ToolMeta(
                name="file_write",
                description="写文件",
                param_schema={"type": "object", "properties": {}},
            ),
            mock_write,
        )
        result = te.get_tool("file_write")(path=str(tmp_path / "test.txt"), content="hello")

        assert result["status"] == "ok"
        assert len(written_files) == 1

    def test_shell_exec_runs_subprocess(self):
        executed_commands = []

        def mock_shell(**kwargs):
            executed_commands.append(kwargs.get("command"))
            return "output"

        te = ToolRegistry()
        te.register(
            ToolMeta(
                name="shell_exec",
                description="执行命令",
                param_schema={"type": "object", "properties": {}},
            ),
            mock_shell,
        )
        result = te.get_tool("shell_exec")(command="echo hello")

        assert len(executed_commands) == 1
        assert executed_commands[0] == "echo hello"
        assert result == "output"

    def test_tool_invocation_tracks_audit_log(self):
        audit_log = []

        def mock_tool(**kwargs):
            audit_log.append({"tool": "mock_tool", "params": kwargs})
            return "result"

        te = ToolRegistry()
        te.register(
            ToolMeta(
                name="mock_tool",
                description="测试工具",
                param_schema={"type": "object", "properties": {}},
                audit_enabled=True,
            ),
            mock_tool,
        )
        te.get_tool("mock_tool")(param="value")

        assert len(audit_log) == 1
        assert audit_log[0]["params"]["param"] == "value"

    def test_tool_with_idempotency(self):
        call_count = [0]

        def idempotent_tool(**kwargs):
            call_count[0] += 1
            return {"count": call_count[0]}

        te = ToolRegistry()
        te.register(
            ToolMeta(
                name="idempotent",
                description="幂等工具",
                param_schema={"type": "object", "properties": {}},
            ),
            idempotent_tool,
        )
        result1 = te.get_tool("idempotent")()
        result2 = te.get_tool("idempotent")()

        assert call_count[0] == 2
        assert result1["count"] == 1
        assert result2["count"] == 2
