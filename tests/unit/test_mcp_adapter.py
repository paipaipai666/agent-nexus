import asyncio
from types import SimpleNamespace

from agentnexus.core.config import MCPServerConfig
from agentnexus.tools.mcp_adapter import MCPToolDescriptor, MCPToolManager


class FakeExitStack:
    async def aclose(self):
        return None


class TestMcpToolManager:
    def test_status_snapshot_reports_runtime_and_failures(self):
        manager = MCPToolManager(
            [
                MCPServerConfig(name="docs", transport="stdio", command="python"),
                MCPServerConfig(name="remote", transport="streamable_http", url="https://example.com/mcp"),
            ]
        )
        manager._started = True
        manager._failures = {"remote": "timeout"}
        manager._server_runtimes["docs"] = SimpleNamespace(tool_names=["mcp_docs__search"])
        manager._tool_descriptors["mcp_docs__search"] = MCPToolDescriptor(
            local_name="mcp_docs__search",
            remote_name="search",
            server_name="docs",
            description="desc",
            param_schema={"type": "object", "properties": {}},
            allowed_agents=["react_agent"],
            risk_level="medium",
            require_hitl=False,
            timeout_sec=30,
            rate_limit_per_min=5,
        )

        snapshot = manager.status_snapshot()

        assert snapshot["started"] is True
        assert snapshot["server_count"] == 2
        assert snapshot["connected_count"] == 1
        assert snapshot["failure_count"] == 1
        assert snapshot["tool_count"] == 1
        assert snapshot["servers"][0]["name"] == "docs"
        assert snapshot["servers"][1]["failure"] == "timeout"

    def test_retry_failed_reconnects_failed_server(self, monkeypatch):
        manager = MCPToolManager([MCPServerConfig(name="remote", transport="stdio", command="python")])
        manager._started = True
        manager._failures = {"remote": "boom"}
        monkeypatch.setattr(manager, "_ensure_sdk_available", lambda: None)

        async def fake_connect(server):
            manager._server_runtimes[server.name] = SimpleNamespace(
                tool_names=["mcp_remote__echo"],
                exit_stack=FakeExitStack(),
            )
            manager._tool_descriptors["mcp_remote__echo"] = MCPToolDescriptor(
                local_name="mcp_remote__echo",
                remote_name="echo",
                server_name=server.name,
                description="desc",
                param_schema={"type": "object", "properties": {}},
                allowed_agents=["react_agent"],
                risk_level="medium",
                require_hitl=False,
                timeout_sec=30,
                rate_limit_per_min=5,
            )

        monkeypatch.setattr(manager, "_connect_server", fake_connect)

        result = asyncio.run(manager._retry_failed_async())

        assert result["retried"] == ["remote"]
        assert result["reconnected"] == ["remote"]
        assert result["failed"] == {}
        assert manager._failures == {}
        assert "mcp_remote__echo" in manager._tool_descriptors

    def test_retry_failed_skips_connected_healthy_server(self, monkeypatch):
        manager = MCPToolManager([MCPServerConfig(name="docs", transport="stdio", command="python")])
        manager._started = True
        manager._server_runtimes["docs"] = SimpleNamespace(
            tool_names=["mcp_docs__search"],
            exit_stack=FakeExitStack(),
        )
        monkeypatch.setattr(manager, "_ensure_sdk_available", lambda: None)

        result = asyncio.run(manager._retry_failed_async(server_name="docs"))

        assert result["retried"] == []
        assert result["skipped"] == ["docs"]
