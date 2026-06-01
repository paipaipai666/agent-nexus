"""Tests for MCP connection lifecycle orchestration."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentnexus.core.config import MCPServerConfig
from agentnexus.tools.mcp_lifecycle import close_all, connect_all, disconnect_server
from agentnexus.tools.mcp_schema import MCPServerState, ServerRuntime


def _make_config(name="test_server"):
    return MCPServerConfig(name=name, transport="stdio", command="echo")


def _make_runtime(config=None, **overrides):
    if config is None:
        config = _make_config()
    attrs = dict(
        config=config,
        session=AsyncMock(),
        exit_stack=SimpleNamespace(aclose=AsyncMock()),
        state=MCPServerState.HEALTHY,
        tool_names=[],
        resource_tool_names=[],
        prompt_tool_names=[],
        resource_descriptors=[],
        resource_templates=[],
        prompt_descriptors=[],
        call_lock=None,
        semaphore=None,
        last_ping_at=0,
        consecutive_failures=0,
        reconnect_attempts=0,
        next_reconnect_at=None,
        last_failure=None,
    )
    attrs.update(overrides)
    return SimpleNamespace(**attrs)


# ---------------------------------------------------------------------------
# connect_all
# ---------------------------------------------------------------------------


class TestConnectAll:
    """Tests for connect_all."""

    def test_all_servers_succeed_no_failures(self):
        server_a = _make_config(name="a")
        server_b = _make_config(name="b")
        connect_server = AsyncMock()
        server_states = {}
        failures = {}

        async def run():
            await connect_all(
                [server_a, server_b],
                connect_server=connect_server,
                server_states=server_states,
                failures=failures,
            )

        asyncio.run(run())
        assert server_states["a"] == MCPServerState.CONNECTING
        assert server_states["b"] == MCPServerState.CONNECTING
        assert connect_server.await_count == 2
        assert failures == {}

    def test_one_server_failures_that_server_disconnected(self):
        server_a = _make_config(name="good")
        server_b = _make_config(name="bad")

        async def fake_connect(server):
            if server.name == "bad":
                raise RuntimeError("connection refused")

        connect_server = AsyncMock(side_effect=fake_connect)
        server_states = {}
        failures = {}

        async def run():
            await connect_all(
                [server_a, server_b],
                connect_server=connect_server,
                server_states=server_states,
                failures=failures,
            )

        asyncio.run(run())
        assert server_states["bad"] == MCPServerState.DISCONNECTED
        assert "bad" in failures
        assert "connection refused" in failures["bad"]
        # good server should have been processed (CONNECTING state set, connect_server called)
        assert server_states["good"] == MCPServerState.CONNECTING

    def test_empty_list_noop(self):
        connect_server = AsyncMock()
        server_states = {}
        failures = {}

        async def run():
            await connect_all(
                [],
                connect_server=connect_server,
                server_states=server_states,
                failures=failures,
            )

        asyncio.run(run())
        assert server_states == {}
        connect_server.assert_not_awaited()


# ---------------------------------------------------------------------------
# disconnect_server
# ---------------------------------------------------------------------------


class TestDisconnectServer:
    """Tests for disconnect_server."""

    @patch("agentnexus.core.hooks.get_hook_manager")
    def test_existing_runtime_state_closed_exit_stack_closed(self, mock_get_hook_mgr):
        mock_hook_mgr = MagicMock()
        mock_get_hook_mgr.return_value = mock_hook_mgr
        runtime = _make_runtime()
        runtime.exit_stack = SimpleNamespace(aclose=AsyncMock())
        server_runtimes = {"test_server": runtime}
        clear_descriptors = MagicMock()

        async def run():
            await disconnect_server(
                "test_server",
                server_runtimes=server_runtimes,
                clear_descriptors=clear_descriptors,
            )

        asyncio.run(run())
        assert "test_server" not in server_runtimes
        assert runtime.state == MCPServerState.CLOSED
        runtime.exit_stack.aclose.assert_awaited_once()
        clear_descriptors.assert_called_once_with("test_server")

    @patch("agentnexus.core.hooks.get_hook_manager")
    def test_nonexistent_name_graceful_return(self, mock_get_hook_mgr):
        mock_hook_mgr = MagicMock()
        mock_get_hook_mgr.return_value = mock_hook_mgr
        server_runtimes = {}
        clear_descriptors = MagicMock()

        async def run():
            await disconnect_server(
                "nonexistent",
                server_runtimes=server_runtimes,
                clear_descriptors=clear_descriptors,
            )

        asyncio.run(run())
        clear_descriptors.assert_called_once_with("nonexistent")


# ---------------------------------------------------------------------------
# close_all
# ---------------------------------------------------------------------------


class TestCloseAll:
    """Tests for close_all."""

    def test_health_task_cancelled_and_gathered(self):
        mock_task = MagicMock()
        mock_task.cancel = MagicMock()
        server = _make_config(name="srv")
        runtime = _make_runtime()
        runtime.exit_stack = SimpleNamespace(aclose=AsyncMock())
        server_runtimes = {"srv": runtime}
        server_states = {"srv": MCPServerState.HEALTHY}
        tool_descriptors = {"t1": "desc"}
        resource_descriptors = {"srv": []}
        resource_template_descriptors = {"srv": []}
        prompt_descriptors = {"srv": []}
        callable_cache = {"cached": True}

        with patch("asyncio.gather", new_callable=AsyncMock, return_value=[None]) as mock_gather:

            async def run():
                return await close_all(
                    servers=[server],
                    health_task=mock_task,
                    server_runtimes=server_runtimes,
                    server_states=server_states,
                    tool_descriptors=tool_descriptors,
                    resource_descriptors=resource_descriptors,
                    resource_template_descriptors=resource_template_descriptors,
                    prompt_descriptors=prompt_descriptors,
                    callable_cache=callable_cache,
                )

            result = asyncio.run(run())

        mock_task.cancel.assert_called_once()
        mock_gather.assert_awaited_once()
        assert result is None

    def test_all_dicts_cleared(self):
        server = _make_config(name="srv")
        runtime = _make_runtime()
        runtime.exit_stack = SimpleNamespace(aclose=AsyncMock())
        server_runtimes = {"srv": runtime}
        server_states = {"srv": MCPServerState.HEALTHY}
        tool_descriptors = {"t1": "desc"}
        resource_descriptors = {"srv": []}
        resource_template_descriptors = {"srv": []}
        prompt_descriptors = {"srv": []}
        callable_cache = {"cached": True}

        async def run():
            return await close_all(
                servers=[server],
                health_task=None,
                server_runtimes=server_runtimes,
                server_states=server_states,
                tool_descriptors=tool_descriptors,
                resource_descriptors=resource_descriptors,
                resource_template_descriptors=resource_template_descriptors,
                prompt_descriptors=prompt_descriptors,
                callable_cache=callable_cache,
            )

        asyncio.run(run())
        assert server_runtimes == {}
        assert tool_descriptors == {}
        assert resource_descriptors == {}
        assert resource_template_descriptors == {}
        assert prompt_descriptors == {}
        assert callable_cache == {}

    def test_all_runtimes_closed(self):
        server = _make_config(name="srv")
        runtime = _make_runtime()
        runtime.exit_stack = SimpleNamespace(aclose=AsyncMock())
        server_runtimes = {"srv": runtime}
        server_states = {}

        async def run():
            return await close_all(
                servers=[server],
                health_task=None,
                server_runtimes=server_runtimes,
                server_states=server_states,
                tool_descriptors={},
                resource_descriptors={},
                resource_template_descriptors={},
                prompt_descriptors={},
                callable_cache={},
            )

        asyncio.run(run())
        assert runtime.state == MCPServerState.CLOSED
        assert server_states["srv"] == MCPServerState.CLOSED
        runtime.exit_stack.aclose.assert_awaited_once()

    def test_health_task_none_no_cancellation(self):
        server = _make_config(name="srv")
        runtime = _make_runtime()
        runtime.exit_stack = SimpleNamespace(aclose=AsyncMock())
        server_runtimes = {"srv": runtime}
        server_states = {}

        async def run():
            return await close_all(
                servers=[server],
                health_task=None,
                server_runtimes=server_runtimes,
                server_states=server_states,
                tool_descriptors={},
                resource_descriptors={},
                resource_template_descriptors={},
                prompt_descriptors={},
                callable_cache={},
            )

        # Should complete without error
        result = asyncio.run(run())
        assert result is None
