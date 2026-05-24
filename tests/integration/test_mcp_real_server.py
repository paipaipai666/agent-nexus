"""Integration test: real MCP server process via stdio transport."""

import os
import tempfile

import pytest

from agentnexus.core.config import MCPServerConfig
from agentnexus.tools.mcp_adapter import MCPToolManager

pytest.importorskip("mcp.server.fastmcp")


@pytest.fixture
def mcp_server_script():
    script_content = '''\
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("test-server")

@mcp.tool()
def echo(message: str) -> str:
    """Echo back message."""
    return f"echo: {message}"

@mcp.tool()
def add(a: int, b: int) -> int:
    """Add two integers."""
    return a + b

mcp.run(transport="stdio")
'''
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8") as f:
        f.write(script_content)
        script_path = f.name
    yield script_path
    if os.path.exists(script_path):
        os.unlink(script_path)


class TestRealMcpServer:
    def test_connect_list_and_call_tool(self, mcp_server_script):
        config = MCPServerConfig(
            name="live-server",
            transport="stdio",
            command="python",
            args=[mcp_server_script],
            timeout_sec=30,
        )
        manager = MCPToolManager([config], startup_timeout=30)
        try:
            manager.start()
            assert manager._started is True

            tool_names = manager.list_tool_names()
            assert "mcp_live_server__echo" in tool_names
            assert "mcp_live_server__add" in tool_names

            result = manager.call_tool("mcp_live_server__echo", {"message": "hello world"})
            assert "echo: hello world" in result

            result = manager.call_tool("mcp_live_server__add", {"a": 2, "b": 3})
            assert "5" in result
        finally:
            manager.close()

    def test_call_nonexistent_tool_raises_key_error(self, mcp_server_script):
        config = MCPServerConfig(
            name="srv",
            transport="stdio",
            command="python",
            args=[mcp_server_script],
            timeout_sec=30,
        )
        manager = MCPToolManager([config], startup_timeout=30)
        try:
            manager.start()
            with pytest.raises(KeyError):
                manager.call_tool("mcp_srv__nonexistent", {})
        finally:
            manager.close()
