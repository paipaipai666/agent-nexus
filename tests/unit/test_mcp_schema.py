"""Tests for MCP adapter state and descriptor models."""

import asyncio
from contextlib import AsyncExitStack
from unittest.mock import MagicMock

from agentnexus.tools.mcp_schema import (
    MCPPromptDescriptor,
    MCPResourceDescriptor,
    MCPServerState,
    MCPToolDescriptor,
    ServerRuntime,
)


class TestMCPServerState:
    """Verify all enum members carry the expected string values."""

    def test_disconnected_value(self):
        assert MCPServerState.DISCONNECTED == "disconnected"

    def test_connecting_value(self):
        assert MCPServerState.CONNECTING == "connecting"

    def test_healthy_value(self):
        assert MCPServerState.HEALTHY == "healthy"

    def test_degraded_value(self):
        assert MCPServerState.DEGRADED == "degraded"

    def test_reconnecting_value(self):
        assert MCPServerState.RECONNECTING == "reconnecting"

    def test_closed_value(self):
        assert MCPServerState.CLOSED == "closed"

    def test_all_members_present(self):
        expected = {"disconnected", "connecting", "healthy", "degraded", "reconnecting", "closed"}
        assert {m.value for m in MCPServerState} == expected


class TestMCPToolDescriptor:
    """Tests for the MCPToolDescriptor dataclass."""

    def test_required_fields(self):
        desc = MCPToolDescriptor(
            local_name="mcp_search__find",
            remote_name="find",
            server_name="search",
            description="Search tool",
            param_schema={"type": "object"},
            allowed_agents=["react_agent"],
            risk_level="medium",
            require_hitl=False,
            timeout_sec=30,
            rate_limit_per_min=10,
        )
        assert desc.local_name == "mcp_search__find"
        assert desc.remote_name == "find"
        assert desc.server_name == "search"
        assert desc.description == "Search tool"
        assert desc.param_schema == {"type": "object"}
        assert desc.allowed_agents == ["react_agent"]
        assert desc.risk_level == "medium"
        assert desc.require_hitl is False
        assert desc.timeout_sec == 30
        assert desc.rate_limit_per_min == 10

    def test_capability_defaults_to_tool(self):
        desc = MCPToolDescriptor(
            local_name="a", remote_name="a", server_name="s",
            description="d", param_schema={},
            allowed_agents=[], risk_level="low",
            require_hitl=False, timeout_sec=10, rate_limit_per_min=5,
        )
        assert desc.capability == "tool"

    def test_custom_capability_value(self):
        desc = MCPToolDescriptor(
            local_name="a", remote_name="a", server_name="s",
            description="d", param_schema={},
            allowed_agents=[], risk_level="low",
            require_hitl=False, timeout_sec=10, rate_limit_per_min=5,
            capability="resource",
        )
        assert desc.capability == "resource"


class TestMCPResourceDescriptor:
    """Tests for the MCPResourceDescriptor dataclass."""

    def test_required_fields(self):
        desc = MCPResourceDescriptor(
            name="docs", uri="file:///docs", server_name="srv",
        )
        assert desc.name == "docs"
        assert desc.uri == "file:///docs"
        assert desc.server_name == "srv"

    def test_description_defaults_empty(self):
        desc = MCPResourceDescriptor(
            name="x", uri="u", server_name="s",
        )
        assert desc.description == ""

    def test_mime_type_defaults_empty(self):
        desc = MCPResourceDescriptor(
            name="x", uri="u", server_name="s",
        )
        assert desc.mime_type == ""

    def test_custom_description_and_mime(self):
        desc = MCPResourceDescriptor(
            name="x", uri="u", server_name="s",
            description="a doc", mime_type="text/plain",
        )
        assert desc.description == "a doc"
        assert desc.mime_type == "text/plain"


class TestMCPPromptDescriptor:
    """Tests for the MCPPromptDescriptor dataclass."""

    def test_required_fields(self):
        desc = MCPPromptDescriptor(
            name="greet", server_name="srv",
        )
        assert desc.name == "greet"
        assert desc.server_name == "srv"

    def test_description_defaults_empty(self):
        desc = MCPPromptDescriptor(name="x", server_name="s")
        assert desc.description == ""

    def test_arguments_defaults_empty_list(self):
        desc = MCPPromptDescriptor(name="x", server_name="s")
        assert desc.arguments == []

    def test_custom_description_and_arguments(self):
        args = [{"name": "user", "required": True}]
        desc = MCPPromptDescriptor(
            name="x", server_name="s",
            description="prompt desc", arguments=args,
        )
        assert desc.description == "prompt desc"
        assert desc.arguments == args


class TestServerRuntime:
    """Tests for the ServerRuntime dataclass."""

    def _make_config(self):
        config = MagicMock()
        config.name = "test_server"
        return config

    def test_post_init_creates_semaphore_when_not_provided(self):
        runtime = ServerRuntime(
            config=self._make_config(),
            session=None,
            exit_stack=AsyncExitStack(),
        )
        assert runtime.semaphore is not None
        assert isinstance(runtime.semaphore, asyncio.Semaphore)

    def test_post_init_does_not_overwrite_explicit_semaphore(self):
        explicit = asyncio.Semaphore(5)
        runtime = ServerRuntime(
            config=self._make_config(),
            session=None,
            exit_stack=AsyncExitStack(),
            semaphore=explicit,
        )
        assert runtime.semaphore is explicit

    def test_default_state_is_healthy(self):
        runtime = ServerRuntime(
            config=self._make_config(),
            session=None,
            exit_stack=AsyncExitStack(),
        )
        assert runtime.state == MCPServerState.HEALTHY

    def test_consecutive_failures_defaults_zero(self):
        runtime = ServerRuntime(
            config=self._make_config(),
            session=None,
            exit_stack=AsyncExitStack(),
        )
        assert runtime.consecutive_failures == 0

    def test_reconnect_attempts_defaults_zero(self):
        runtime = ServerRuntime(
            config=self._make_config(),
            session=None,
            exit_stack=AsyncExitStack(),
        )
        assert runtime.reconnect_attempts == 0

    def test_next_reconnect_at_defaults_none(self):
        runtime = ServerRuntime(
            config=self._make_config(),
            session=None,
            exit_stack=AsyncExitStack(),
        )
        assert runtime.next_reconnect_at is None

    def test_last_failure_defaults_none(self):
        runtime = ServerRuntime(
            config=self._make_config(),
            session=None,
            exit_stack=AsyncExitStack(),
        )
        assert runtime.last_failure is None
