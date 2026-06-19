"""Tests for MCP config, retry, status snapshot, and capabilities."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentnexus.core.config import MCPServerConfig
from agentnexus.tools.mcp_adapter import (
    MCPServerState,
    MCPToolDescriptor,
    MCPToolManager,
    create_mcp_manager_from_settings,
)

from .conftest import FakeExitStack, _make_descriptor


# ── create_mcp_manager_from_settings ────────────────────────────


class TestCreateMcpManagerFromSettings:
    def test_returns_none_when_mcp_disabled(self):
        settings = SimpleNamespace(mcp_enabled=False)
        assert create_mcp_manager_from_settings(settings) is None

    def test_returns_none_when_no_enabled_servers(self):
        settings = SimpleNamespace(mcp_enabled=True, mcp_startup_timeout=15, mcp_servers=[])
        assert create_mcp_manager_from_settings(settings) is None

    def test_returns_manager_when_all_servers_disabled(self, monkeypatch):
        started = False

        def fake_start(self):
            nonlocal started
            started = True

        monkeypatch.setattr(MCPToolManager, "start", fake_start)
        settings = SimpleNamespace(
            mcp_enabled=True,
            mcp_startup_timeout=15,
            mcp_servers=[MCPServerConfig(name="x", transport="stdio", command="python", enabled=False)],
        )
        manager = create_mcp_manager_from_settings(settings)
        assert manager is not None
        assert started is True
        assert manager.server_names() == ["x"]

    def test_returns_manager_for_configured_servers(self, monkeypatch):
        started = False

        def fake_start(self):
            nonlocal started
            started = True

        monkeypatch.setattr(MCPToolManager, "start", fake_start)
        settings = SimpleNamespace(
            mcp_enabled=True,
            mcp_startup_timeout=15,
            mcp_servers=[MCPServerConfig(name="x", transport="stdio", command="python")],
        )
        manager = create_mcp_manager_from_settings(settings)
        assert manager is not None
        assert started is True
        assert manager.server_names() == ["x"]

    def test_capability_config_controls_initial_enabled_servers(self, monkeypatch, temp_agentnexus_home):
        captured = {}

        def fake_start(self):
            captured["servers"] = [server.name for server in self._servers]

        monkeypatch.setattr(MCPToolManager, "start", fake_start)
        (temp_agentnexus_home / "config.yaml").write_text(
            "capabilities:\n  mcp_servers:\n    x: true\n",
            encoding="utf-8",
        )
        settings = SimpleNamespace(
            mcp_enabled=True,
            mcp_startup_timeout=15,
            mcp_servers=[MCPServerConfig(name="x", transport="stdio", command="python")],
        )

        manager = create_mcp_manager_from_settings(settings)

        assert manager is not None
        assert captured["servers"] == ["x"]


class TestCreateMcpManagerFromSettingsExtended:
    def test_start_failure_propagates(self, monkeypatch):
        """If manager.start() raises, create_mcp_manager_from_settings must propagate."""
        original_start = MCPToolManager.start

        def failing_start(self):
            raise RuntimeError("start failed")

        monkeypatch.setattr(MCPToolManager, "start", failing_start)
        settings = SimpleNamespace(
            mcp_enabled=True,
            mcp_startup_timeout=15,
            mcp_servers=[MCPServerConfig(name="x", transport="stdio", command="python")],
        )
        with pytest.raises(RuntimeError, match="start failed"):
            create_mcp_manager_from_settings(settings)

        monkeypatch.setattr(MCPToolManager, "start", original_start)


# ── retry_failed complete flow ──────────────────────────────────


class TestRetryFailedFullFlow:
    def test_retry_failed_sync_submit(self, monkeypatch):
        """retry_failed() calls _submit with _retry_failed_async."""
        manager = MCPToolManager([MCPServerConfig(name="x", transport="stdio", command="python")])
        manager._started = True
        manager._loop = SimpleNamespace()
        submitted = []

        def fake_submit(coro, timeout):
            submitted.append(coro)
            return {"retried": ["x"], "reconnected": ["x"], "skipped": [], "failed": {}}

        monkeypatch.setattr(manager, "_submit", fake_submit)
        monkeypatch.setattr(manager, "_ensure_sdk_available", lambda: None)

        result = manager.retry_failed()
        assert result["retried"] == ["x"]

    def test_retry_failed_re_registers_descriptors(self, monkeypatch):
        """After reconnect, new tool descriptors should be populated."""
        manager = MCPToolManager([MCPServerConfig(name="api", transport="stdio", command="python")])
        manager._started = True
        manager._loop = SimpleNamespace()
        manager._loop.call_soon_threadsafe = MagicMock()
        manager._loop.stop = MagicMock()

        async def fake_retry(server_name=None):
            manager._server_runtimes["api"] = SimpleNamespace(tool_names=["mcp_api__echo"])
            manager._tool_descriptors["mcp_api__echo"] = _make_descriptor(
                local_name="mcp_api__echo",
                remote_name="echo",
                server_name="api",
            )
            return {
                "retried": ["api"],
                "reconnected": ["api"],
                "skipped": [],
                "failed": {},
                "snapshot": manager.status_snapshot(),
            }

        monkeypatch.setattr(manager, "_retry_failed_async", fake_retry)
        monkeypatch.setattr(manager, "_submit", lambda coro, timeout: asyncio.run(coro))
        result = manager.retry_failed(server_name="api")
        assert "mcp_api__echo" in manager._tool_descriptors
        assert result["reconnected"] == ["api"]

    def test_retry_skips_connected_healthy(self, monkeypatch):
        """A healthy connected server should be skipped, not retried."""
        manager = MCPToolManager([MCPServerConfig(name="ok", transport="stdio", command="python")])
        manager._started = True
        manager._server_runtimes["ok"] = SimpleNamespace(
            tool_names=["mcp_ok__tool"],
            exit_stack=FakeExitStack(),
        )
        monkeypatch.setattr(manager, "_ensure_sdk_available", lambda: None)

        result = asyncio.run(manager._retry_failed_async())
        assert result["retried"] == []
        assert result["skipped"] == ["ok"]

    def test_retry_partial_failure(self, monkeypatch):
        """When one server reconnects and another still fails, both outcomes reported."""
        manager = MCPToolManager([
            MCPServerConfig(name="good", transport="stdio", command="python"),
            MCPServerConfig(name="bad", transport="stdio", command="python"),
        ])
        manager._started = True
        monkeypatch.setattr(manager, "_ensure_sdk_available", lambda: None)

        connected = []

        async def fake_connect(server):
            if server.name == "bad":
                raise ConnectionError("still broken")
            connected.append(server.name)
            local_name = f"mcp_{server.name}__tool"
            manager._server_runtimes[server.name] = SimpleNamespace(
                tool_names=[local_name],
                exit_stack=FakeExitStack(),
            )
            manager._tool_descriptors[local_name] = _make_descriptor(
                local_name=local_name, server_name=server.name,
            )

        monkeypatch.setattr(manager, "_connect_server", fake_connect)

        manager._failures["good"] = "was down"
        manager._failures["bad"] = "was down"

        result = asyncio.run(manager._retry_failed_async())
        assert result["reconnected"] == ["good"]
        assert "bad" in result["failed"]
        assert manager._failures.get("bad") == "still broken"
        assert "good" not in manager._failures
        assert "mcp_good__tool" in manager._tool_descriptors

    def test_retry_with_server_name_filter(self, monkeypatch):
        """Only the named server should be retried."""
        manager = MCPToolManager([
            MCPServerConfig(name="a", transport="stdio", command="python"),
            MCPServerConfig(name="b", transport="stdio", command="python"),
        ])
        manager._started = True
        manager._failures["a"] = "err"
        manager._failures["b"] = "err"
        monkeypatch.setattr(manager, "_ensure_sdk_available", lambda: None)

        async def fake_connect(server):
            manager._server_runtimes[server.name] = SimpleNamespace(
                tool_names=[f"mcp_{server.name}__tool"],
                exit_stack=FakeExitStack(),
            )

        monkeypatch.setattr(manager, "_connect_server", fake_connect)

        result = asyncio.run(manager._retry_failed_async(server_name="a"))
        assert result["reconnected"] == ["a"]
        assert "b" not in result["reconnected"]


# ── status_snapshot validation ──────────────────────────────────


class TestStatusSnapshotValidation:
    def test_status_snapshot_mixed_servers(self):
        """status_snapshot must report connected, failed, and empty states correctly."""
        manager = MCPToolManager([
            MCPServerConfig(name="connected", transport="stdio", command="python"),
            MCPServerConfig(name="failed", transport="stdio", command="python"),
            MCPServerConfig(name="empty", transport="stdio", command="python"),
        ])
        manager._started = True
        manager._server_runtimes["connected"] = SimpleNamespace(
            tool_names=["mcp_connected__tool1", "mcp_connected__tool2"],
        )
        manager._tool_descriptors["mcp_connected__tool1"] = _make_descriptor(
            local_name="mcp_connected__tool1", server_name="connected",
        )
        manager._tool_descriptors["mcp_connected__tool2"] = _make_descriptor(
            local_name="mcp_connected__tool2", server_name="connected",
        )
        manager._failures["failed"] = "connection refused"

        snapshot = manager.status_snapshot()

        assert snapshot["started"] is True
        assert snapshot["server_count"] == 3
        assert snapshot["connected_count"] == 1
        assert snapshot["failure_count"] == 1
        assert snapshot["tool_count"] == 2

        servers_by_name = {s["name"]: s for s in snapshot["servers"]}
        assert servers_by_name["connected"]["connected"] is True
        assert servers_by_name["connected"]["tool_names"] == ["mcp_connected__tool1", "mcp_connected__tool2"]
        assert servers_by_name["connected"]["failure"] is None

        assert servers_by_name["failed"]["connected"] is False
        assert servers_by_name["failed"]["failure"] == "connection refused"

        assert servers_by_name["empty"]["connected"] is False
        assert servers_by_name["empty"]["tool_names"] == []
        assert servers_by_name["empty"]["failure"] is None

    def test_status_snapshot_no_servers(self):
        """status_snapshot must handle empty server list."""
        manager = MCPToolManager([])
        snapshot = manager.status_snapshot()
        assert snapshot["server_count"] == 0
        assert snapshot["connected_count"] == 0
        assert snapshot["failure_count"] == 0
        assert snapshot["tool_count"] == 0


# ── descriptor_signature tests ──────────────────────────────────


class TestMCPDescriptorSignature:
    def _make_descriptor(self, **overrides):
        defaults = dict(
            local_name="test",
            remote_name="test",
            server_name="srv",
            description="desc",
            param_schema={},
            allowed_agents=["agent"],
            risk_level="medium",
            require_hitl=False,
            timeout_sec=30,
            rate_limit_per_min=10,
            capability="tool",
        )
        defaults.update(overrides)
        return MCPToolDescriptor(**defaults)

    def test_signature_consistent(self):
        d1 = self._make_descriptor()
        d2 = self._make_descriptor()
        sig1 = MCPToolManager._descriptor_signature(d1)
        sig2 = MCPToolManager._descriptor_signature(d2)
        assert sig1 == sig2

    def test_signature_changes_on_diff(self):
        d1 = self._make_descriptor(description="aaa")
        d2 = self._make_descriptor(description="bbb")
        sig1 = MCPToolManager._descriptor_signature(d1)
        sig2 = MCPToolManager._descriptor_signature(d2)
        assert sig1 != sig2

    def test_signature_includes_all_fields(self):
        d = self._make_descriptor(
            description="search tool",
            param_schema={"type": "object"},
            allowed_agents=["react_agent"],
            risk_level="high",
            require_hitl=True,
            timeout_sec=60,
            rate_limit_per_min=5,
            capability="tool",
        )
        sig = MCPToolManager._descriptor_signature(d)
        payload = json.loads(sig)
        assert payload["description"] == "search tool"
        assert payload["risk"] == "high"
        assert payload["allowed"] == ["react_agent"]
        assert payload["hitl"] is True
        assert payload["timeout"] == 60
        assert payload["rate"] == 5
        assert payload["capability"] == "tool"


# ── Full MCP capabilities integration ──────────────────────────


class TestFullMcpCapabilities:
    def test_imports_resource_and_prompt_bridge_tools(self):
        config = MCPServerConfig(name="demo", transport="stdio", command="python")
        manager = MCPToolManager([config])

        session = SimpleNamespace(
            list_tools=AsyncMock(return_value=SimpleNamespace(tools=[])),
            list_resources=AsyncMock(return_value=SimpleNamespace(resources=[
                SimpleNamespace(name="Doc", uri="file:///doc.md", description="Docs", mimeType="text/markdown")
            ])),
            list_resource_templates=AsyncMock(return_value=SimpleNamespace(resourceTemplates=[
                SimpleNamespace(name="ById", uriTemplate="file:///{id}", description="By id")
            ])),
            list_prompts=AsyncMock(return_value=SimpleNamespace(prompts=[
                SimpleNamespace(name="review", description="Review prompt", arguments=[])
            ])),
            read_resource=AsyncMock(return_value=SimpleNamespace(contents=[SimpleNamespace(text="doc body")])),
            get_prompt=AsyncMock(return_value=SimpleNamespace(
                messages=[SimpleNamespace(role="user", content="review")]
            )),
        )
        runtime = SimpleNamespace(
            config=config,
            session=session,
            tool_names=[],
            resource_tool_names=[],
            prompt_tool_names=[],
            resource_descriptors=[],
            resource_templates=[],
            prompt_descriptors=[],
        )

        asyncio.run(manager._import_server_capabilities(runtime))

        assert "mcp_demo__list_resources" in manager._tool_descriptors
        assert "mcp_demo__read_resource" in manager._tool_descriptors
        assert "mcp_demo__list_resource_templates" in manager._tool_descriptors
        assert "mcp_demo__list_prompts" in manager._tool_descriptors
        assert "mcp_demo__get_prompt" in manager._tool_descriptors
        assert "file:///doc.md" in manager.auto_context()
        assert "review" in manager.auto_context()

    def test_read_resource_and_get_prompt_wrappers(self):
        config = MCPServerConfig(name="demo", transport="stdio", command="python")
        manager = MCPToolManager([config])
        session = SimpleNamespace(
            read_resource=AsyncMock(return_value=SimpleNamespace(contents=[SimpleNamespace(text="resource text")])),
            get_prompt=AsyncMock(return_value=SimpleNamespace(
                messages=[SimpleNamespace(role="user", content="prompt text")]
            )),
        )
        manager._server_runtimes["demo"] = SimpleNamespace(
            config=config,
            session=session,
            state=MCPServerState.HEALTHY,
            semaphore=asyncio.Semaphore(4),
        )
        read_desc = manager._internal_descriptor(
            config, "mcp_demo__read_resource", "read_resource", "read", {"uri": {"type": "string"}}, "resource", ["uri"]
        )
        prompt_desc = manager._internal_descriptor(
            config, "mcp_demo__get_prompt", "get_prompt", "prompt", {"name": {"type": "string"}}, "prompt", ["name"]
        )

        assert asyncio.run(manager._call_tool_async(read_desc, {"uri": "file:///doc.md"})) == "resource text"
        prompt_text = asyncio.run(manager._call_tool_async(prompt_desc, {"name": "review"}))
        assert "prompt text" in prompt_text

    def test_health_check_marks_degraded_and_schedules_reconnect(self):
        config = MCPServerConfig(
            name="demo",
            transport="stdio",
            command="python",
            health_check_interval_sec=1,
            reconnect_initial_delay_sec=1,
        )
        manager = MCPToolManager([config])
        session = SimpleNamespace(send_ping=AsyncMock(side_effect=ConnectionError("lost")))
        runtime = SimpleNamespace(
            config=config,
            session=session,
            state=MCPServerState.HEALTHY,
            last_ping_at=0,
            consecutive_failures=0,
            reconnect_attempts=0,
            next_reconnect_at=None,
            last_failure=None,
        )
        manager._server_runtimes["demo"] = runtime
        manager._server_states["demo"] = MCPServerState.HEALTHY

        asyncio.run(manager._health_check_once())

        assert manager._server_states["demo"] == MCPServerState.DEGRADED
        assert manager._failures["demo"] == "lost"
        assert runtime.reconnect_attempts == 1
        assert runtime.next_reconnect_at is not None
