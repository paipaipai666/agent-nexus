"""Tests for agentnexus.tools.register_all_tools — registration metadata."""

from unittest.mock import MagicMock

from agentnexus.tools import register_all_tools
from agentnexus.tools.registry import RiskLevel
from agentnexus.tools.tool_executor import ToolExecutor


class TestRegisterCount:
    def test_registers_all_tools_by_default(self):
        executor = ToolExecutor()
        register_all_tools(executor)
        names = executor.registry.list_tools()
        assert len(names) == 11

    def test_registers_correct_tool_names(self):
        executor = ToolExecutor()
        register_all_tools(executor)
        names = executor.registry.list_tools()
        expected = {
            "memory_search", "memory_save", "grep_search", "web_search",
            "kb_search", "file_read", "file_list", "file_write",
            "python_execute", "shell_exec", "subagent_run",
        }
        assert set(names) == expected


class TestIncludeTools:
    def test_include_tools_filter(self):
        executor = ToolExecutor()
        register_all_tools(executor, include_tools={"web_search", "file_read"})
        names = executor.registry.list_tools()
        assert set(names) == {"web_search", "file_read"}

    def test_include_tools_nonexistent(self):
        executor = ToolExecutor()
        register_all_tools(executor, include_tools={"nonexistent_tool"})
        assert executor.registry.list_tools() == []

    def test_include_tools_empty_set(self):
        executor = ToolExecutor()
        register_all_tools(executor, include_tools=set())
        assert executor.registry.list_tools() == []


class TestNonInteractive:
    def test_non_interactive_disables_hitl(self):
        executor = ToolExecutor()
        register_all_tools(executor, non_interactive=True)
        for name in ("file_write", "python_execute", "shell_exec"):
            meta = executor.registry._tools[name][0]
            assert meta.require_hitl is False, f"{name} should have require_hitl=False"

    def test_interactive_enables_hitl(self):
        executor = ToolExecutor()
        register_all_tools(executor, non_interactive=False)
        for name in ("file_write", "python_execute", "shell_exec"):
            meta = executor.registry._tools[name][0]
            assert meta.require_hitl is True, f"{name} should have require_hitl=True"


class TestRiskLevels:
    def test_low_risk_tools(self):
        executor = ToolExecutor()
        register_all_tools(executor)
        low_tools = ("memory_search", "memory_save", "grep_search",
                     "web_search", "kb_search", "file_read", "file_list")
        for name in low_tools:
            meta = executor.registry._tools[name][0]
            assert meta.risk_level == RiskLevel.LOW, f"{name} should be low risk"

    def test_medium_risk_tool(self):
        executor = ToolExecutor()
        register_all_tools(executor)
        meta = executor.registry._tools["file_write"][0]
        assert meta.risk_level == RiskLevel.MEDIUM

    def test_high_risk_tools(self):
        executor = ToolExecutor()
        register_all_tools(executor)
        for name in ("python_execute", "shell_exec"):
            meta = executor.registry._tools[name][0]
            assert meta.risk_level == RiskLevel.HIGH, f"{name} should be high risk"


class TestRateLimits:
    def test_rate_limits_set(self):
        executor = ToolExecutor()
        register_all_tools(executor, enable_subagent=False)
        names = executor.registry.list_tools()
        no_limit = {"python_execute", "shell_exec"}
        for name in names:
            meta = executor.registry._tools[name][0]
            if name in no_limit:
                assert meta.rate_limit_per_min == 0, f"{name} should have no rate limit"
            else:
                assert meta.rate_limit_per_min > 0, f"{name} should have rate_limit_per_min > 0"

    def test_specific_rates(self):
        executor = ToolExecutor()
        register_all_tools(executor)
        meta = executor.registry._tools["file_read"][0]
        assert meta.rate_limit_per_min == 30

    def test_no_rate_limit_on_exec_tools(self):
        executor = ToolExecutor()
        register_all_tools(executor)
        for name in ("python_execute", "shell_exec"):
            meta = executor.registry._tools[name][0]
            assert meta.rate_limit_per_min == 0, f"{name} should have rate_limit_per_min=0"


class TestTimeoutAndAgents:
    def test_python_execute_config(self):
        executor = ToolExecutor()
        register_all_tools(executor)
        meta = executor.registry._tools["python_execute"][0]
        assert meta.timeout_sec == 60
        assert meta.allowed_agents == ["react_agent", "subagent_executor"]

    def test_shell_exec_config(self):
        executor = ToolExecutor()
        register_all_tools(executor)
        meta = executor.registry._tools["shell_exec"][0]
        assert meta.timeout_sec == 60

    def test_file_write_timeout(self):
        executor = ToolExecutor()
        register_all_tools(executor)
        meta = executor.registry._tools["file_write"][0]
        assert meta.timeout_sec == 10


class TestParamSchemas:
    def test_web_search_requires_query(self):
        executor = ToolExecutor()
        register_all_tools(executor)
        meta = executor.registry._tools["web_search"][0]
        assert meta.param_schema["required"] == ["query"]

    def test_web_search_search_depth_enum(self):
        executor = ToolExecutor()
        register_all_tools(executor)
        meta = executor.registry._tools["web_search"][0]
        depth = meta.param_schema["properties"]["search_depth"]
        assert depth["enum"] == ["basic", "advanced"]

    def test_file_write_requires_path_and_content(self):
        executor = ToolExecutor()
        register_all_tools(executor)
        meta = executor.registry._tools["file_write"][0]
        assert meta.param_schema["required"] == ["path", "content"]

    def test_kb_search_exposes_advanced_rag_params(self):
        executor = ToolExecutor()
        register_all_tools(executor)
        meta = executor.registry._tools["kb_search"][0]
        props = meta.param_schema["properties"]

        assert props["view"]["enum"] == ["section", "chunk"]
        assert "source" in props
        assert "file_format" in props
        assert "section_title" in props
        assert "page_number" in props
        assert props["block_type"]["enum"] == ["paragraph", "list", "heading", "code"]
        assert "has_code" in props
        assert "has_list" in props
        assert "heading_depth" in props


class FakeMCPManager:
    def __init__(self):
        self.register_calls = []

    def register_tools(self, executor, include_tools=None):
        self.register_calls.append(include_tools)
        executor.registerTool(
            "mcp_demo__echo",
            "[MCP:demo] echo",
            lambda message: message,
            param_schema={
                "type": "object",
                "properties": {"message": {"type": "string"}},
                "required": ["message"],
            },
            allowed_agents=["react_agent", "subagent_explorer"],
            risk_level="medium",
            require_hitl=True,
            timeout_sec=45,
            rate_limit_per_min=7,
        )
        return ["mcp_demo__echo"]


class TestMCPRegistration:
    def test_registers_mcp_tools_when_manager_present(self):
        executor = ToolExecutor()
        manager = FakeMCPManager()
        register_all_tools(executor, mcp_manager=manager)
        names = executor.registry.list_tools()
        assert "mcp_demo__echo" in names
        meta = executor.registry._tools["mcp_demo__echo"][0]
        assert meta.risk_level == RiskLevel.MEDIUM
        assert meta.require_hitl is True
        assert manager.register_calls == [None]

    def test_include_tools_filters_mcp_tools(self):
        executor = ToolExecutor()
        manager = FakeMCPManager()
        register_all_tools(executor, include_tools={"mcp_demo__echo"}, mcp_manager=manager, enable_subagent=False)
        assert executor.registry.list_tools() == ["mcp_demo__echo"]
        assert manager.register_calls == [{"mcp_demo__echo"}]


class TestDisableSubagent:
    def test_subagent_disabled(self):
        executor = ToolExecutor()
        register_all_tools(executor, enable_subagent=False,
                           non_interactive=True, llm_client=MagicMock())
        assert executor.getTool("subagent_run") is None

    def test_subagent_enabled(self):
        executor = ToolExecutor()
        register_all_tools(executor, non_interactive=True, llm_client=MagicMock())
        assert executor.getTool("subagent_run") is not None
