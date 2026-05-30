"""Security tests for MCP adapter — input validation, access control, resource protection."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from agentnexus.core.config import MCPServerConfig
from agentnexus.tools.mcp_adapter import (
    MCPToolDescriptor,
    MCPToolManager,
    _sanitize_name,
)
from agentnexus.tools.registry import ToolRegistry


def _make_descriptor(**overrides) -> MCPToolDescriptor:
    defaults = dict(
        local_name="mcp_docs__search",
        remote_name="search",
        server_name="docs",
        description="[MCP:docs] search",
        param_schema={"type": "object", "properties": {}},
        allowed_agents=["react_agent"],
        risk_level="medium",
        require_hitl=False,
        timeout_sec=30,
        rate_limit_per_min=5,
    )
    defaults.update(overrides)
    return MCPToolDescriptor(**defaults)


# ── 1. Name sanitization — injection prevention ─────────────────


class TestNameSanitizationSecurity:
    def test_shell_injection_escaped(self):
        """Shell metacharacters in server/tool names must be sanitized."""
        assert ";" not in _sanitize_name("foo;rm -rf /")
        assert "|" not in _sanitize_name("bar|cat /etc/passwd")
        assert "`" not in _sanitize_name("baz`id`")
        assert "$" not in _sanitize_name("qux$(whoami)")

    def test_path_traversal_escaped(self):
        """Path traversal sequences must be sanitized."""
        result = _sanitize_name("../etc/passwd")
        assert ".." not in result
        assert "/" not in result

    def test_unicode_normalized(self):
        """Non-ASCII unicode chars must be sanitized."""
        result = _sanitize_name("héllo wörld")
        assert all(c.isascii() and (c.isalnum() or c == "_") for c in result)

    def test_name_length_does_not_overflow(self):
        """Very long names must not cause issues."""
        long_name = "a" * 10000
        result = _sanitize_name(long_name)
        assert len(result) <= 10000
        assert result == long_name

    def test_tool_name_prefix_not_confusable(self):
        """Tool names starting with mcp_ must be consistently formatted."""
        config = MCPServerConfig(name="test-server", transport="stdio", command="python")
        manager = MCPToolManager([])
        name = manager._build_local_tool_name(config, "search")
        assert name.startswith("mcp_")
        assert "__" in name
        assert name.count("__") == 1  # exactly one double-underscore separator


# ── 2. Access control — allowed_agents enforcement ───────────────


class TestAgentAccessControl:
    def test_subagent_cannot_call_restricted_tool(self):
        """Tools with restricted allowed_agents must be hidden from subagents."""
        manager = MCPToolManager([])
        manager._tool_descriptors["restricted"] = _make_descriptor(
            local_name="restricted",
            allowed_agents=["react_agent"],
        )
        manager._tool_descriptors["open"] = _make_descriptor(
            local_name="open",
            allowed_agents=["*"],
        )
        subagent_names = manager.list_subagent_tool_names()
        assert "restricted" not in subagent_names
        assert "open" in subagent_names

    def test_allowed_agents_list_matches_subagent_prefix(self):
        """Tools allowed_agents containing subagent_* must be accessible."""
        manager = MCPToolManager([])
        manager._tool_descriptors["t1"] = _make_descriptor(
            local_name="t1",
            allowed_agents=["react_agent", "subagent_explorer"],
        )
        manager._tool_descriptors["t2"] = _make_descriptor(
            local_name="t2",
            allowed_agents=["subagent_executor"],
        )
        names = manager.list_subagent_tool_names()
        assert "t1" in names
        assert "t2" in names

    def test_register_tools_preserves_allowed_agents(self):
        """Executor registration must preserve allowed_agents from descriptor."""
        manager = MCPToolManager([])
        manager._tool_descriptors["mcp_api__admin"] = _make_descriptor(
            local_name="mcp_api__admin",
            allowed_agents=["admin_agent"],
        )
        executor = ToolRegistry()
        manager.register_tools(executor)
        meta = executor._tools["mcp_api__admin"][0]
        assert meta.allowed_agents == ["admin_agent"]

    def test_rbac_enforced_on_invoke(self):
        """Registry must reject calls from unauthorized agents."""
        executor = ToolRegistry()
        executor.register_tool(
            "mcp_api__secret",
            "secret tool",
            lambda: "secret",
            allowed_agents=["admin"],
        )
        with pytest.raises(PermissionError):
            executor.invoke("mcp_api__secret", {}, caller="unauthorized_agent")


# ── 3. Risk level propagation ────────────────────────────────────


class TestRiskLevelPropagation:
    def test_high_risk_level_propagates(self):
        manager = MCPToolManager([])
        config = MCPServerConfig(
            name="danger", transport="stdio", command="python",
            risk_level="high",
        )
        tool = SimpleNamespace(name="exec", description="Run code", inputSchema={})
        descriptor = manager._build_descriptor(config, tool)
        assert descriptor.risk_level == "high"

    def test_low_risk_level_propagates(self):
        manager = MCPToolManager([])
        config = MCPServerConfig(
            name="safe", transport="stdio", command="python",
            risk_level="low",
        )
        tool = SimpleNamespace(name="read", description="Read file", inputSchema={})
        descriptor = manager._build_descriptor(config, tool)
        assert descriptor.risk_level == "low"

    def test_registry_rejects_unknown_risk_level_in_config(self):
        with pytest.raises(ValueError, match="风险等级"):
            MCPServerConfig(name="x", transport="stdio", command="python", risk_level="critical")


# ── 4. HITL propagation ──────────────────────────────────────────


class TestHITLPropagation:
    def test_require_hitl_true_propagates(self):
        manager = MCPToolManager([])
        config = MCPServerConfig(
            name="risky", transport="stdio", command="python",
            require_hitl=True,
        )
        tool = SimpleNamespace(name="delete", description="Delete", inputSchema={})
        descriptor = manager._build_descriptor(config, tool)
        assert descriptor.require_hitl is True

    def test_hitl_registered_on_executor(self):
        manager = MCPToolManager([])
        manager._tool_descriptors["mcp_api__delete"] = _make_descriptor(
            local_name="mcp_api__delete",
            require_hitl=True,
        )
        executor = ToolRegistry()
        manager.register_tools(executor)
        meta = executor._tools["mcp_api__delete"][0]
        assert meta.require_hitl is True

    def test_hitl_blocks_when_no_approver(self):
        executor = ToolRegistry()
        executor.register_tool(
            "mcp_api__delete",
            "delete",
            lambda: "deleted",
            require_hitl=True,
        )
        result = executor.invoke("mcp_api__delete", {}, caller="react_agent")
        assert "blocked" in result


# ── 5. Rate limiting ─────────────────────────────────────────────


class TestRateLimitPropagation:
    def test_rate_limit_propagates_to_executor(self):
        manager = MCPToolManager([])
        manager._tool_descriptors["mcp_api__search"] = _make_descriptor(
            local_name="mcp_api__search",
            rate_limit_per_min=3,
        )
        executor = ToolRegistry()
        manager.register_tools(executor)
        meta = executor._tools["mcp_api__search"][0]
        assert meta.rate_limit_per_min == 3

    def test_rate_limit_exceeded_raises(self):
        executor = ToolRegistry()
        executor.register_tool(
            "mcp_api__search",
            "search",
            lambda: "ok",
            rate_limit_per_min=1,
        )
        executor.invoke("mcp_api__search", {}, caller="react_agent")
        with pytest.raises(RuntimeError, match="Rate limit exceeded"):
            executor.invoke("mcp_api__search", {}, caller="react_agent")


# ── 6. Timeout propagation ───────────────────────────────────────


class TestTimeoutPropagation:
    def test_timeout_propagates_to_executor(self):
        manager = MCPToolManager([])
        manager._tool_descriptors["mcp_api__slow"] = _make_descriptor(
            local_name="mcp_api__slow",
            timeout_sec=5,
        )
        executor = ToolRegistry()
        manager.register_tools(executor)
        meta = executor._tools["mcp_api__slow"][0]
        assert meta.timeout_sec == 5


# ── 7. Include / exclude tool filtering ─────────────────────────


class TestToolFilteringSecurity:
    def test_exclude_tools_blocks_import(self):
        manager = MCPToolManager([])
        config = MCPServerConfig(
            name="api", transport="stdio", command="python",
            exclude_tools=["dangerous_tool"],
        )
        tool_dangerous = SimpleNamespace(name="dangerous_tool", description="Dangerous", inputSchema={})
        tool_safe = SimpleNamespace(name="safe_tool", description="Safe", inputSchema={})
        assert manager._build_descriptor(config, tool_dangerous) is None
        assert manager._build_descriptor(config, tool_safe) is not None

    def test_include_tools_restricts_to_allowlist(self):
        manager = MCPToolManager([])
        config = MCPServerConfig(
            name="api", transport="stdio", command="python",
            include_tools=["only_this"],
        )
        def _ns(n):
            return SimpleNamespace(name=n, description="", inputSchema={})
        assert manager._build_descriptor(config, _ns("only_this")) is not None
        assert manager._build_descriptor(config, _ns("other")) is None

    def test_include_tools_empty_allows_all(self):
        manager = MCPToolManager([])
        config = MCPServerConfig(name="api", transport="stdio", command="python")
        ns = SimpleNamespace(name="any", description="", inputSchema={})
        assert manager._build_descriptor(config, ns) is not None

    def test_tool_name_collision_cannot_override_descriptor(self):
        """_ensure_unique_name must prevent silent overwrite of existing descriptors."""
        manager = MCPToolManager([])
        manager._tool_descriptors["mcp_api__search"] = _make_descriptor(
            local_name="mcp_api__search",
            description="original",
        )
        unique = manager._ensure_unique_name("mcp_api__search")
        assert unique != "mcp_api__search"
        assert unique == "mcp_api__search_2"


# ── 8. Transport validation security ─────────────────────────────


class TestTransportSecurity:
    def test_http_transport_requires_url(self):
        with pytest.raises(ValueError, match="必须提供 url"):
            MCPServerConfig(name="x", transport="streamable_http")

    def test_stdio_transport_requires_command(self):
        with pytest.raises(ValueError, match="必须提供 command"):
            MCPServerConfig(name="x", transport="stdio")

    def test_invalid_transport_rejected(self):
        with pytest.raises(ValueError, match="不支持的"):
            MCPServerConfig(name="x", transport="tcp")

    def test_url_must_have_scheme(self):
        with pytest.raises(ValueError, match="必须以 http"):
            MCPServerConfig(name="x", transport="streamable_http", url="localhost:8080/mcp")

    def test_url_with_http_scheme_ok(self):
        config = MCPServerConfig(name="x", transport="streamable_http", url="http://localhost:8080/mcp")
        assert config.url == "http://localhost:8080/mcp"

    def test_url_with_https_scheme_ok(self):
        config = MCPServerConfig(name="x", transport="streamable_http", url="https://mcp.example.com")
        assert config.url == "https://mcp.example.com"


# ── 9. Environment variable exposure risk ────────────────────────


class TestEnvExposureSecurity:
    def test_env_dict_does_not_leak_to_tool_descriptor(self):
        """Env vars should stay in config, not leak to MCPToolDescriptor."""
        config = MCPServerConfig(
            name="api", transport="stdio", command="python",
            env={"API_KEY": "sk-123456", "SECRET": "s3cr3t"},
        )
        tool = SimpleNamespace(name="search", description="Search", inputSchema={})
        manager = MCPToolManager([])
        descriptor = manager._build_descriptor(config, tool)
        # Descriptor should not contain env information
        descriptor_dict = vars(descriptor)
        assert "env" not in descriptor_dict
        assert "API_KEY" not in str(descriptor_dict)
        assert "sk-123456" not in str(descriptor_dict)


# ── 10. SDK availability enforcement ────────────────────────────


class TestSdkAvailability:
    def test_ensure_sdk_available_raises_on_missing(self, monkeypatch):
        import builtins

        original_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "mcp":
                raise ImportError("No module named 'mcp'")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr("builtins.__import__", mock_import)
        with pytest.raises(RuntimeError, match="MCP SDK"):
            MCPToolManager._ensure_sdk_available()


# ── 11. Tool result content safety ───────────────────────────────


class TestToolResultSafety:
    def test_normalize_result_handles_large_content(self):
        """Very large tool results must not crash."""
        from agentnexus.tools.mcp_adapter import _normalize_tool_result

        large_text = "x" * 100000
        result = SimpleNamespace(
            content=[SimpleNamespace(text=large_text)],
            structuredContent=None,
            isError=False,
        )
        text = _normalize_tool_result(result)
        assert len(text) == 100000

    def test_normalize_result_with_error_flag(self):
        """Tool errors with isError=True must propagate the error text."""
        manager = MCPToolManager([])

        async def fake_call_tool(name, arguments=None):
            return SimpleNamespace(
                content=[SimpleNamespace(text="access denied")],
                isError=True,
                is_error=True,
            )

        mock_session = SimpleNamespace(call_tool=fake_call_tool)
        manager._server_runtimes["api"] = SimpleNamespace(
            session=mock_session,
            call_lock=asyncio.Lock(),
        )
        descriptor = _make_descriptor(server_name="api", remote_name="admin_op")
        with pytest.raises(RuntimeError, match="access denied"):
            asyncio.run(manager._call_tool_async(descriptor, {}))

    def test_normalize_result_with_error_flag_empty_content(self):
        """Tool errors with isError=True and no text must use normalize fallback."""
        manager = MCPToolManager([])

        async def fake_call_tool(name, arguments=None):
            return SimpleNamespace(
                content=[],
                isError=True,
                is_error=True,
            )

        mock_session = SimpleNamespace(call_tool=fake_call_tool)
        manager._server_runtimes["api"] = SimpleNamespace(
            session=mock_session,
            call_lock=asyncio.Lock(),
        )
        descriptor = _make_descriptor(server_name="api", remote_name="admin_op")
        with pytest.raises(RuntimeError, match="未返回文本内容"):
            asyncio.run(manager._call_tool_async(descriptor, {}))


# ── 12. Disabled server isolation ────────────────────────────────


class TestDisabledServerIsolation:
    def test_disabled_server_never_connected(self, monkeypatch):
        """A server with enabled=False must never be connected."""
        connected = []

        async def fake_connect(self, config):
            connected.append(config.name)

        monkeypatch.setattr(MCPToolManager, "_connect_server", fake_connect)
        monkeypatch.setattr(MCPToolManager, "_ensure_sdk_available", lambda self: None)

        manager = MCPToolManager([
            MCPServerConfig(name="enabled", transport="stdio", command="python"),
            MCPServerConfig(name="disabled", transport="stdio", command="python", enabled=False),
        ])
        asyncio.run(manager._connect_all())
        assert connected == ["enabled"]
