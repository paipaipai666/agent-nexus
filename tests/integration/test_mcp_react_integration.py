"""Integration tests: MCP tools registered on ToolRegistry, called through ReActAgent."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agentnexus.agents.re_act_agent import ReActAgent
from agentnexus.tools.mcp_adapter import MCPToolDescriptor
from agentnexus.tools.registry import ToolRegistry


def _make_descriptor(**overrides) -> MCPToolDescriptor:
    defaults = dict(
        local_name="mcp_demo__echo",
        remote_name="echo",
        server_name="demo",
        description="[MCP:demo] echo",
        param_schema={"type": "object", "properties": {"message": {"type": "string"}}, "required": ["message"]},
        allowed_agents=["*"],
        risk_level="low",
        require_hitl=False,
        timeout_sec=30,
        rate_limit_per_min=0,
    )
    defaults.update(overrides)
    return MCPToolDescriptor(**defaults)


class FakeMCPManager:
    """Minimal MCP manager that returns canned results."""

    def __init__(self):
        self._tool_descriptors = {}
        self._call_results: dict[str, str] = {}

    def add_tool(self, descriptor: MCPToolDescriptor, result: str = "ok"):
        self._tool_descriptors[descriptor.local_name] = descriptor
        self._call_results[descriptor.local_name] = result

    def list_tool_names(self) -> list[str]:
        return list(self._tool_descriptors.keys())

    def register_tools(self, executor: ToolRegistry, include_tools: set[str] | None = None) -> list[str]:
        registered = []
        for desc in self._tool_descriptors.values():
            if include_tools is not None and desc.local_name not in include_tools:
                continue
            executor.register_tool(
                desc.local_name, desc.description,
                self._make_callable(desc.local_name),
                param_schema=desc.param_schema,
                allowed_agents=desc.allowed_agents,
                risk_level=desc.risk_level,
                require_hitl=desc.require_hitl,
                timeout_sec=desc.timeout_sec,
                rate_limit_per_min=desc.rate_limit_per_min,
            )
            registered.append(desc.local_name)
        return registered

    def _make_callable(self, local_name: str):
        results = self._call_results

        def _call(**params):
            return results.get(local_name, "ok")
        return _call


class TestMcpToolRegistrationOnExecutor:
    def test_mcp_tools_listed_as_available(self):
        executor = ToolRegistry()
        manager = FakeMCPManager()
        manager.add_tool(_make_descriptor())
        manager.add_tool(_make_descriptor(local_name="mcp_demo__search", remote_name="search"))

        manager.register_tools(executor)
        available = executor.get_available_tools("react_agent")
        assert "mcp_demo__echo" in available
        assert "mcp_demo__search" in available
        assert "[MCP:demo]" in available

    def test_mcp_tools_filtered_by_include(self):
        executor = ToolRegistry()
        manager = FakeMCPManager()
        manager.add_tool(_make_descriptor(local_name="mcp_demo__echo"))
        manager.add_tool(_make_descriptor(local_name="mcp_demo__delete"))

        manager.register_tools(executor, include_tools={"mcp_demo__echo"})
        assert executor.get_tool("mcp_demo__echo") is not None
        assert executor.get_tool("mcp_demo__delete") is None

    def test_mcp_tool_rbac_respected(self):
        executor = ToolRegistry()
        manager = FakeMCPManager()
        manager.add_tool(_make_descriptor(
            local_name="mcp_admin__secret",
            allowed_agents=["admin"],
        ))
        manager.register_tools(executor)

        with pytest.raises(PermissionError):
            executor.invoke("mcp_admin__secret", {}, caller="react_agent")

    def test_mcp_tool_hitl_blocks_without_approver(self):
        executor = ToolRegistry()
        manager = FakeMCPManager()
        manager.add_tool(_make_descriptor(
            local_name="mcp_risky__delete",
            param_schema={"type": "object", "properties": {"id": {"type": "string"}}},
            require_hitl=True,
        ))
        manager.register_tools(executor)

        result = executor.invoke("mcp_risky__delete", {"id": "42"}, caller="react_agent")
        assert "blocked" in result

    def test_mcp_tool_audit_logged(self):
        executor = ToolRegistry()
        manager = FakeMCPManager()
        manager.add_tool(_make_descriptor(local_name="mcp_demo__echo"))
        manager.register_tools(executor)

        executor.invoke("mcp_demo__echo", {"message": "hello"}, caller="react_agent")
        log = executor.get_audit_log()
        assert len(log) == 1
        assert log[0].tool_name == "mcp_demo__echo"

    def test_mcp_tool_returns_string_result_through_registry(self):
        executor = ToolRegistry()
        manager = FakeMCPManager()
        manager.add_tool(_make_descriptor(local_name="mcp_demo__echo"), result="echo: hello")

        manager.register_tools(executor)
        result = executor.invoke("mcp_demo__echo", {"message": "hello"}, caller="react_agent")
        assert result == "echo: hello"


class TestMcpToolViaReActAgent:
    def test_agent_execute_tool_calls_mcp(self):
        executor = ToolRegistry()
        manager = FakeMCPManager()
        manager.add_tool(_make_descriptor(local_name="mcp_demo__echo"), result="echo: hello")
        manager.register_tools(executor)

        agent = ReActAgent(MagicMock(), executor, agent_id="react_agent")
        result = agent._execute_tool("mcp_demo__echo", {"message": "hello"})
        assert result == "echo: hello"

    def test_agent_execute_tool_reports_failure(self):
        executor = ToolRegistry()
        manager = FakeMCPManager()
        manager.add_tool(_make_descriptor(local_name="mcp_demo__echo"), result="ok")
        manager.register_tools(executor)

        agent = ReActAgent(MagicMock(), executor, agent_id="react_agent")
        result = agent._execute_tool("nonexistent_mcp_tool", {})
        assert "错误" in result

    def test_agent_execute_tool_hitl_blocked(self):
        executor = ToolRegistry()
        manager = FakeMCPManager()
        manager.add_tool(_make_descriptor(
            local_name="mcp_risky__delete",
            param_schema={"type": "object", "properties": {"id": {"type": "string"}}},
            require_hitl=True,
        ))
        manager.register_tools(executor)

        agent = ReActAgent(MagicMock(), executor, agent_id="react_agent")
        result = agent._execute_tool("mcp_risky__delete", {"id": "42"})
        assert "blocked" in result

    def test_agent_execute_tool_rbac_blocked(self):
        executor = ToolRegistry()
        manager = FakeMCPManager()
        manager.add_tool(_make_descriptor(
            local_name="mcp_admin__secret",
            allowed_agents=["admin"],
        ))
        manager.register_tools(executor)

        agent = ReActAgent(MagicMock(), executor, agent_id="react_agent")
        result = agent._execute_tool("mcp_admin__secret", {})
        assert "错误" in result
        assert "not allowed" in result

    def test_agent_execute_tool_rate_limited(self):
        executor = ToolRegistry()
        manager = FakeMCPManager()
        manager.add_tool(_make_descriptor(
            local_name="mcp_demo__search",
            param_schema={"type": "object"},
            rate_limit_per_min=1,
        ))
        manager.register_tools(executor)

        agent = ReActAgent(MagicMock(), executor, agent_id="react_agent")
        agent._execute_tool("mcp_demo__search", {"q": "test"})
        result = agent._execute_tool("mcp_demo__search", {"q": "test"})
        assert "错误" in result
        assert "Rate limit" in result

    def test_mcp_tool_name_in_available_tools(self):
        executor = ToolRegistry()
        manager = FakeMCPManager()
        manager.add_tool(_make_descriptor(local_name="mcp_demo__echo"))
        manager.register_tools(executor)

        agent = ReActAgent(MagicMock(), executor, agent_id="react_agent")
        tools_str = agent.tool_executor.get_available_tools("react_agent")
        assert "mcp_demo__echo" in tools_str

    def test_mcp_tool_listed_in_openai_tools(self):
        executor = ToolRegistry()
        manager = FakeMCPManager()
        manager.add_tool(_make_descriptor(local_name="mcp_demo__echo"))
        manager.register_tools(executor)

        openai_tools = executor.to_openai_tools("react_agent")
        names = [t["function"]["name"] for t in openai_tools]
        assert "mcp_demo__echo" in names
