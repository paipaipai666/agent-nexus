"""Tests for MCP connection and transport: HTTP, stdio, connect_all."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentnexus.core.config import MCPServerConfig
from agentnexus.tools.mcp_adapter import MCPToolManager

# ── HTTP transport connection tests ──────────────────────────────


class TestHttpConnectServer:
    def test_http_connect_success(self, monkeypatch):
        manager = MCPToolManager(
            [MCPServerConfig(name="api", transport="streamable_http", url="https://api.example.com/mcp")]
        )
        monkeypatch.setattr(manager, "_ensure_sdk_available", lambda: None)
        monkeypatch.setattr(manager, "_build_descriptor", lambda c, t: None)

        read_stream = SimpleNamespace(read=MagicMock())
        write_stream = SimpleNamespace(write=MagicMock())
        streamable_result = (read_stream, write_stream)

        fake_session = MagicMock()
        fake_session.initialize = AsyncMock()
        fake_session.list_tools = AsyncMock(return_value=SimpleNamespace(tools=[]))
        fake_session.__aenter__.return_value = fake_session

        with (
            patch("mcp.ClientSession", return_value=fake_session),
            patch("mcp.client.streamable_http.streamable_http_client") as mock_http,
        ):
            mock_http.return_value.__aenter__.return_value = streamable_result
            asyncio.run(manager._connect_server(manager._servers[0]))

        assert "api" in manager._server_runtimes
        runtime = manager._server_runtimes["api"]
        assert runtime.session is fake_session
        assert runtime.config.name == "api"
        assert runtime.call_lock is not None

    def test_http_connect_handles_single_element_result(self, monkeypatch):
        manager = MCPToolManager(
            [MCPServerConfig(name="api", transport="streamable_http", url="https://api.example.com/mcp")]
        )
        monkeypatch.setattr(manager, "_ensure_sdk_available", lambda: None)
        monkeypatch.setattr(manager, "_build_descriptor", lambda c, t: None)

        with (
            patch("mcp.ClientSession") as mock_session_cls,
            patch("mcp.client.streamable_http.streamable_http_client") as mock_http,
        ):
            fake_session = MagicMock()
            fake_session.initialize = AsyncMock()
            fake_session.__aenter__.return_value = fake_session
            mock_session_cls.return_value = fake_session
            mock_http.return_value.__aenter__.return_value = (SimpleNamespace(),)

            with pytest.raises(RuntimeError, match="Unexpected MCP HTTP transport result"):
                asyncio.run(manager._connect_server(manager._servers[0]))

    def test_http_connect_propagates_timeout(self, monkeypatch):
        manager = MCPToolManager(
            [MCPServerConfig(name="api", transport="streamable_http", url="https://api.example.com/mcp")],
            startup_timeout=0.01,
        )
        monkeypatch.setattr(manager, "_ensure_sdk_available", lambda: None)

        async def slow_initialize():
            await asyncio.sleep(100)

        with (
            patch("mcp.ClientSession") as mock_session_cls,
            patch("mcp.client.streamable_http.streamable_http_client") as mock_http,
        ):
            fake_session = MagicMock()
            fake_session.initialize = slow_initialize
            fake_session.__aenter__.return_value = fake_session
            mock_session_cls.return_value = fake_session
            mock_http.return_value.__aenter__.return_value = (
                SimpleNamespace(), SimpleNamespace(),
            )

            with pytest.raises((TimeoutError, asyncio.TimeoutError)):
                asyncio.run(manager._connect_server(manager._servers[0]))


# ── stdio transport _connect_server tests ────────────────────────


class TestStdioConnectServer:
    def test_stdio_connect_success(self, monkeypatch):
        """_connect_server must successfully connect via stdio and register tools."""
        manager = MCPToolManager([MCPServerConfig(name="demo", transport="stdio", command="python")])
        monkeypatch.setattr(manager, "_ensure_sdk_available", lambda: None)

        mock_tool = SimpleNamespace(
            name="echo",
            description="Echo tool",
            inputSchema={"type": "object", "properties": {"msg": {"type": "string"}}},
        )
        tools_result = SimpleNamespace(tools=[mock_tool])
        fake_session = MagicMock()
        fake_session.initialize = AsyncMock()
        fake_session.list_tools = AsyncMock(return_value=tools_result)
        fake_session.__aenter__.return_value = fake_session

        @asynccontextmanager
        async def fake_stdio_client(params):
            yield SimpleNamespace(read=MagicMock()), SimpleNamespace(write=MagicMock())

        with (
            patch("mcp.ClientSession", return_value=fake_session),
            patch("mcp.client.stdio.stdio_client", side_effect=fake_stdio_client),
            patch("mcp.StdioServerParameters"),
        ):
            asyncio.run(manager._connect_server(manager._servers[0]))

        assert "demo" in manager._server_runtimes
        runtime = manager._server_runtimes["demo"]
        assert runtime.session is fake_session
        assert runtime.call_lock is not None
        assert "mcp_demo__echo" in manager._tool_descriptors
        assert runtime.tool_names == ["mcp_demo__echo"]

    def test_stdio_connect_skips_tool_without_name(self, monkeypatch):
        """Tools without a name should be skipped."""
        manager = MCPToolManager([MCPServerConfig(name="demo", transport="stdio", command="python")])
        monkeypatch.setattr(manager, "_ensure_sdk_available", lambda: None)

        mock_tool = SimpleNamespace(name=None, description="no name", inputSchema=None)
        tools_result = SimpleNamespace(tools=[mock_tool])
        fake_session = MagicMock()
        fake_session.initialize = AsyncMock()
        fake_session.list_tools = AsyncMock(return_value=tools_result)
        fake_session.__aenter__.return_value = fake_session

        @asynccontextmanager
        async def fake_stdio_client(params):
            yield SimpleNamespace(read=MagicMock()), SimpleNamespace(write=MagicMock())

        with (
            patch("mcp.ClientSession", return_value=fake_session),
            patch("mcp.client.stdio.stdio_client", side_effect=fake_stdio_client),
            patch("mcp.StdioServerParameters"),
        ):
            asyncio.run(manager._connect_server(manager._servers[0]))

        assert "demo" in manager._server_runtimes
        assert manager._tool_descriptors == {}

    def test_stdio_connect_error_cleans_up_stack(self, monkeypatch):
        """When connect fails, the exit stack must be cleaned up."""
        manager = MCPToolManager([MCPServerConfig(name="demo", transport="stdio", command="python")])
        monkeypatch.setattr(manager, "_ensure_sdk_available", lambda: None)

        @asynccontextmanager
        async def fake_stdio_client(params):
            yield SimpleNamespace(read=MagicMock()), SimpleNamespace(write=MagicMock())

        with (
            patch("mcp.ClientSession") as mock_session_cls,
            patch("mcp.client.stdio.stdio_client", side_effect=fake_stdio_client),
            patch("mcp.StdioServerParameters"),
        ):
            fake_session = MagicMock()
            fake_session.__aenter__.return_value = fake_session
            fake_session.initialize.side_effect = RuntimeError("init failed")
            mock_session_cls.return_value = fake_session

            with pytest.raises(RuntimeError, match="init failed"):
                asyncio.run(manager._connect_server(manager._servers[0]))

        assert "demo" not in manager._server_runtimes
        assert manager._tool_descriptors == {}

    def test_stdio_connect_includes_only_config_respected(self, monkeypatch):
        """include_tools config must filter which tools are registered."""
        config = MCPServerConfig(name="demo", transport="stdio", command="python", include_tools=["search"])
        manager = MCPToolManager([config])
        monkeypatch.setattr(manager, "_ensure_sdk_available", lambda: None)

        tools = [
            SimpleNamespace(name="search", description="Search", inputSchema=None),
            SimpleNamespace(name="delete", description="Delete", inputSchema=None),
        ]
        tools_result = SimpleNamespace(tools=tools)
        fake_session = MagicMock()
        fake_session.initialize = AsyncMock()
        fake_session.list_tools = AsyncMock(return_value=tools_result)
        fake_session.__aenter__.return_value = fake_session

        @asynccontextmanager
        async def fake_stdio_client(params):
            yield SimpleNamespace(read=MagicMock()), SimpleNamespace(write=MagicMock())

        with (
            patch("mcp.ClientSession", return_value=fake_session),
            patch("mcp.client.stdio.stdio_client", side_effect=fake_stdio_client),
            patch("mcp.StdioServerParameters"),
        ):
            asyncio.run(manager._connect_server(manager._servers[0]))

        assert "mcp_demo__search" in manager._tool_descriptors
        assert "mcp_demo__delete" not in manager._tool_descriptors


# ── _connect_server: list_tools() failure paths ─────────────────


class TestConnectServerFailurePaths:
    def test_list_tools_failure_cleans_up_http(self, monkeypatch):
        """HTTP: if session.list_tools() raises, exit stack must be cleaned up and error propagated."""
        manager = MCPToolManager(
            [MCPServerConfig(name="api", transport="streamable_http", url="https://api.example.com/mcp")]
        )
        monkeypatch.setattr(manager, "_ensure_sdk_available", lambda: None)

        read_stream = SimpleNamespace(read=MagicMock())
        write_stream = SimpleNamespace(write=MagicMock())

        with (
            patch("mcp.ClientSession") as mock_session_cls,
            patch("mcp.client.streamable_http.streamable_http_client") as mock_http,
        ):
            fake_session = MagicMock()
            fake_session.initialize = AsyncMock()
            fake_session.list_tools = AsyncMock(side_effect=RuntimeError("list_tools failed"))
            fake_session.__aenter__.return_value = fake_session
            mock_session_cls.return_value = fake_session
            mock_http.return_value.__aenter__.return_value = (read_stream, write_stream)

            with pytest.raises(RuntimeError, match="list_tools failed"):
                asyncio.run(manager._connect_server(manager._servers[0]))

        assert "api" not in manager._server_runtimes
        assert manager._tool_descriptors == {}

    def test_list_tools_failure_cleans_up_stdio(self, monkeypatch):
        """Stdio: if session.list_tools() raises, exit stack must be cleaned up and error propagated."""
        manager = MCPToolManager([MCPServerConfig(name="demo", transport="stdio", command="python")])
        monkeypatch.setattr(manager, "_ensure_sdk_available", lambda: None)

        @asynccontextmanager
        async def fake_stdio_client(params):
            yield SimpleNamespace(read=MagicMock()), SimpleNamespace(write=MagicMock())

        with (
            patch("mcp.ClientSession") as mock_session_cls,
            patch("mcp.client.stdio.stdio_client", side_effect=fake_stdio_client),
            patch("mcp.StdioServerParameters"),
        ):
            fake_session = MagicMock()
            fake_session.initialize = AsyncMock()
            fake_session.list_tools = AsyncMock(side_effect=ValueError("tools broken"))
            fake_session.__aenter__.return_value = fake_session
            mock_session_cls.return_value = fake_session

            with pytest.raises(ValueError, match="tools broken"):
                asyncio.run(manager._connect_server(manager._servers[0]))

        assert "demo" not in manager._server_runtimes
        assert manager._tool_descriptors == {}

    def test_list_tools_timeout_cleans_up(self, monkeypatch):
        """If session.list_tools() times out, stack must be cleaned up."""
        manager = MCPToolManager(
            [MCPServerConfig(name="slow", transport="stdio", command="python")],
            startup_timeout=0.01,
        )
        monkeypatch.setattr(manager, "_ensure_sdk_available", lambda: None)

        async def endless_list():
            await asyncio.sleep(100)

        @asynccontextmanager
        async def fake_stdio_client(params):
            yield SimpleNamespace(read=MagicMock()), SimpleNamespace(write=MagicMock())

        with (
            patch("mcp.ClientSession") as mock_session_cls,
            patch("mcp.client.stdio.stdio_client", side_effect=fake_stdio_client),
            patch("mcp.StdioServerParameters"),
        ):
            fake_session = MagicMock()
            fake_session.initialize = AsyncMock()
            fake_session.list_tools = endless_list
            fake_session.__aenter__.return_value = fake_session
            mock_session_cls.return_value = fake_session

            with pytest.raises((TimeoutError, asyncio.TimeoutError)):
                asyncio.run(manager._connect_server(manager._servers[0]))

        assert "slow" not in manager._server_runtimes
        assert manager._tool_descriptors == {}


# ── HTTP transport initialize() failure ─────────────────────────


class TestHttpInitializeFailure:
    def test_http_initialize_failure_cleans_up_stack(self, monkeypatch):
        """HTTP: session.initialize() must clean up exit stack on failure."""
        manager = MCPToolManager(
            [MCPServerConfig(name="api", transport="streamable_http", url="https://api.example.com/mcp")]
        )
        monkeypatch.setattr(manager, "_ensure_sdk_available", lambda: None)

        with (
            patch("mcp.ClientSession") as mock_session_cls,
            patch("mcp.client.streamable_http.streamable_http_client") as mock_http,
        ):
            fake_session = MagicMock()
            fake_session.initialize = AsyncMock(side_effect=RuntimeError("init failed"))
            fake_session.__aenter__.return_value = fake_session
            mock_session_cls.return_value = fake_session
            mock_http.return_value.__aenter__.return_value = (
                SimpleNamespace(), SimpleNamespace(),
            )

            with pytest.raises(RuntimeError, match="init failed"):
                asyncio.run(manager._connect_server(manager._servers[0]))

        assert "api" not in manager._server_runtimes
        assert manager._tool_descriptors == {}


# ── _connect_all complete test suite ────────────────────────────


class TestConnectAllSuite:
    def test_connect_all_all_succeed(self, monkeypatch):
        """_connect_all must record no failures when all servers connect."""
        manager = MCPToolManager([
            MCPServerConfig(name="a", transport="stdio", command="python"),
            MCPServerConfig(name="b", transport="stdio", command="python"),
        ])
        monkeypatch.setattr(manager, "_ensure_sdk_available", lambda: None)
        connected = []

        async def fake_connect(server):
            connected.append(server.name)

        monkeypatch.setattr(manager, "_connect_server", fake_connect)

        asyncio.run(manager._connect_all())
        assert manager._failures == {}
        assert connected == ["a", "b"]

    def test_connect_all_sdk_unavailable_raises(self, monkeypatch):
        """_ensure_sdk_available is called before the loop, so failure must propagate."""
        manager = MCPToolManager([
            MCPServerConfig(name="a", transport="stdio", command="python"),
        ])

        def fail_sdk():
            raise RuntimeError("MCP SDK not available")

        monkeypatch.setattr(manager, "_ensure_sdk_available", fail_sdk)

        with pytest.raises(RuntimeError, match="MCP SDK not available"):
            asyncio.run(manager._connect_all())

    def test_connect_all_mixed(self, monkeypatch):
        """Partial failures: some servers connect, some fail."""
        manager = MCPToolManager([
            MCPServerConfig(name="good", transport="stdio", command="python"),
            MCPServerConfig(name="bad", transport="stdio", command="python"),
        ])
        monkeypatch.setattr(manager, "_ensure_sdk_available", lambda: None)

        async def fake_connect(server):
            if server.name == "bad":
                raise ConnectionError("refused")

        monkeypatch.setattr(manager, "_connect_server", fake_connect)

        asyncio.run(manager._connect_all())
        assert "good" not in manager._failures
        assert "bad" in manager._failures
        assert "refused" in manager._failures["bad"]
