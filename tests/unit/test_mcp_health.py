"""Tests for MCP health check loop and reconnection logic."""

import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from agentnexus.core.config import MCPServerConfig
from agentnexus.tools.mcp_adapter import (
    MCPServerState,
    MCPToolDescriptor,
    MCPToolManager,
)


class TestMCPHealthCheckOnce:
    """Test _health_check_once method."""

    def _make_runtime(self, config, **overrides):
        attrs = dict(
            config=config,
            state=MCPServerState.HEALTHY,
            session=AsyncMock(),
            last_ping_at=0,
            consecutive_failures=0,
            reconnect_attempts=0,
            next_reconnect_at=None,
            last_failure=None,
            exit_stack=SimpleNamespace(aclose=AsyncMock()),
        )
        attrs.update(overrides)
        return SimpleNamespace(**attrs)

    def test_healthy_server_pinged(self):
        config = MCPServerConfig(
            name="test", transport="stdio", command="python",
            health_check_interval_sec=30, timeout_sec=10,
        )
        manager = MCPToolManager([config])
        runtime = self._make_runtime(config)
        manager._server_runtimes[config.name] = runtime

        asyncio.run(manager._health_check_once())

        runtime.session.send_ping.assert_awaited()
        assert runtime.consecutive_failures == 0
        assert runtime.last_failure is None
        assert config.name not in manager._failures

    def test_healthy_server_skipped_within_interval(self):
        config = MCPServerConfig(
            name="test", transport="stdio", command="python",
            health_check_interval_sec=30, timeout_sec=10,
        )
        manager = MCPToolManager([config])
        runtime = self._make_runtime(config, last_ping_at=time.time())
        manager._server_runtimes[config.name] = runtime

        asyncio.run(manager._health_check_once())

        runtime.session.send_ping.assert_not_awaited()

    def test_healthy_ping_failure_marks_degraded(self):
        config = MCPServerConfig(
            name="test", transport="stdio", command="python",
            health_check_interval_sec=30, timeout_sec=10,
        )
        manager = MCPToolManager([config])
        session = AsyncMock()
        session.send_ping.side_effect = RuntimeError("connection lost")
        runtime = self._make_runtime(config, session=session)
        manager._server_runtimes[config.name] = runtime

        asyncio.run(manager._health_check_once())

        assert runtime.state == MCPServerState.DEGRADED
        assert runtime.consecutive_failures == 1
        assert "connection lost" in runtime.last_failure
        assert manager._server_states[config.name] == MCPServerState.DEGRADED
        assert config.name in manager._failures

    def test_consecutive_failures_incremented(self):
        config = MCPServerConfig(
            name="test", transport="stdio", command="python",
            health_check_interval_sec=30, timeout_sec=10,
        )
        manager = MCPToolManager([config])
        session = AsyncMock()
        session.send_ping.side_effect = RuntimeError("fail")
        runtime = self._make_runtime(config, session=session, consecutive_failures=2)
        manager._server_runtimes[config.name] = runtime

        asyncio.run(manager._health_check_once())

        assert runtime.consecutive_failures == 3

    def test_reconnect_scheduled_for_disconnected(self):
        config = MCPServerConfig(name="test", transport="stdio", command="python")
        manager = MCPToolManager([config])
        manager._failures[config.name] = "previous error"
        manager._reconnect_server = AsyncMock()

        asyncio.run(manager._health_check_once())

        manager._reconnect_server.assert_called_once_with(config, None)

    def test_failures_dict_cleared_on_successful_ping(self):
        config = MCPServerConfig(
            name="test", transport="stdio", command="python",
            health_check_interval_sec=30, timeout_sec=10,
        )
        manager = MCPToolManager([config])
        runtime = self._make_runtime(config)
        manager._server_runtimes[config.name] = runtime
        manager._failures[config.name] = "old error"

        asyncio.run(manager._health_check_once())

        assert config.name not in manager._failures


class TestMCPHealthLoop:
    """Test _health_loop method."""

    def test_health_loop_loops_with_interval(self):
        config = MCPServerConfig(
            name="test", transport="stdio", command="python",
            health_check_interval_sec=30,
        )
        manager = MCPToolManager([config])
        manager._health_check_once = AsyncMock()

        async def stop_sleep(delay):
            manager._closing = True

        with patch.object(asyncio, "sleep", stop_sleep):
            asyncio.run(manager._health_loop())

        manager._health_check_once.assert_called_once()

    def test_health_loop_stops_on_closing(self):
        config = MCPServerConfig(name="test", transport="stdio", command="python")
        manager = MCPToolManager([config])
        manager._closing = True
        manager._health_check_once = AsyncMock()

        asyncio.run(manager._health_loop())

        manager._health_check_once.assert_not_called()

    def test_health_loop_min_interval_one_second(self):
        config = MCPServerConfig(
            name="test", transport="stdio", command="python",
            health_check_interval_sec=1,
        )
        manager = MCPToolManager([config])
        manager._health_check_once = AsyncMock()

        sleeps = []

        async def capture_sleep(delay):
            sleeps.append(delay)
            manager._closing = True

        with patch.object(asyncio, "sleep", capture_sleep):
            asyncio.run(manager._health_loop())

        assert len(sleeps) >= 1
        assert all(s >= 1 for s in sleeps)


class TestMCPReconnect:
    """Test _reconnect_server method."""

    def test_reconnect_disconnects_then_connects(self):
        config = MCPServerConfig(name="test", transport="stdio", command="python")
        manager = MCPToolManager([config])
        runtime = SimpleNamespace(
            config=config,
            state=MCPServerState.HEALTHY,
            next_reconnect_at=0,
            exit_stack=SimpleNamespace(aclose=AsyncMock()),
        )
        manager._server_runtimes[config.name] = runtime
        manager._disconnect_server = AsyncMock()
        manager._connect_server = AsyncMock()

        asyncio.run(manager._reconnect_server(config, runtime))

        manager._disconnect_server.assert_called_once_with(config.name)
        manager._connect_server.assert_called_once_with(config)

    def test_reconnect_skipped_when_before_next_reconnect_time(self):
        config = MCPServerConfig(name="test", transport="stdio", command="python")
        manager = MCPToolManager([config])
        runtime = SimpleNamespace(
            config=config,
            next_reconnect_at=time.time() + 9999,
            state=MCPServerState.HEALTHY,
        )
        manager._disconnect_server = AsyncMock()
        manager._connect_server = AsyncMock()

        asyncio.run(manager._reconnect_server(config, runtime))

        manager._disconnect_server.assert_not_called()
        manager._connect_server.assert_not_called()

    def test_reconnect_sets_state_to_reconnecting(self):
        config = MCPServerConfig(name="test", transport="stdio", command="python")
        manager = MCPToolManager([config])
        runtime = SimpleNamespace(
            config=config,
            state=MCPServerState.HEALTHY,
            next_reconnect_at=0,
            exit_stack=SimpleNamespace(aclose=AsyncMock()),
        )
        manager._server_runtimes[config.name] = runtime
        manager._disconnect_server = AsyncMock()
        manager._connect_server = AsyncMock()

        asyncio.run(manager._reconnect_server(config, runtime))

        assert runtime.state == MCPServerState.RECONNECTING
        assert manager._server_states[config.name] == MCPServerState.RECONNECTING


class TestMCPShouldAttemptReconnect:
    """Test _should_attempt_reconnect method."""

    def test_closing_returns_false(self):
        config = MCPServerConfig(name="test", transport="stdio", command="python")
        manager = MCPToolManager([config])
        manager._closing = True
        assert manager._should_attempt_reconnect(config, None, 0) is False

    def test_no_runtime_and_in_failures(self):
        config = MCPServerConfig(name="test", transport="stdio", command="python")
        manager = MCPToolManager([config])
        manager._failures["test"] = "error"
        assert manager._should_attempt_reconnect(config, None, 0) is True

    def test_no_runtime_and_not_in_failures(self):
        config = MCPServerConfig(name="test", transport="stdio", command="python")
        manager = MCPToolManager([config])
        assert manager._should_attempt_reconnect(config, None, 0) is False

    def test_runtime_with_future_reconnect(self):
        config = MCPServerConfig(name="test", transport="stdio", command="python")
        manager = MCPToolManager([config])
        runtime = SimpleNamespace(next_reconnect_at=9999)
        assert manager._should_attempt_reconnect(config, runtime, 100) is False

    def test_runtime_with_past_reconnect(self):
        config = MCPServerConfig(name="test", transport="stdio", command="python")
        manager = MCPToolManager([config])
        runtime = SimpleNamespace(next_reconnect_at=10)
        assert manager._should_attempt_reconnect(config, runtime, 100) is True


class TestMCPDescriptorSignature:
    """Test _descriptor_signature method."""

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


class TestMCPScheduleReconnect:
    """Test _schedule_reconnect method."""

    def test_exponential_backoff(self):
        config = MCPServerConfig(
            name="test", transport="stdio", command="python",
            reconnect_initial_delay_sec=2,
            reconnect_max_delay_sec=60,
        )
        manager = MCPToolManager([config])

        runtime = SimpleNamespace(
            config=config, reconnect_attempts=0, next_reconnect_at=None,
        )

        with patch("time.time", return_value=1000):
            asyncio.run(manager._schedule_reconnect(config, runtime))
        assert runtime.reconnect_attempts == 1
        assert runtime.next_reconnect_at == 1002  # 1000 + 2

        with patch("time.time", return_value=2000):
            asyncio.run(manager._schedule_reconnect(config, runtime))
        assert runtime.reconnect_attempts == 2
        assert runtime.next_reconnect_at == 2004  # 2000 + 4

        with patch("time.time", return_value=3000):
            asyncio.run(manager._schedule_reconnect(config, runtime))
        assert runtime.reconnect_attempts == 3
        assert runtime.next_reconnect_at == 3008  # 3000 + 8

    def test_max_attempts_stops_reconnect(self):
        config = MCPServerConfig(
            name="test", transport="stdio", command="python",
            reconnect_max_attempts=3, reconnect_initial_delay_sec=1,
        )
        manager = MCPToolManager([config])
        runtime = SimpleNamespace(
            config=config, reconnect_attempts=3, next_reconnect_at=123,
        )

        asyncio.run(manager._schedule_reconnect(config, runtime))

        assert runtime.reconnect_attempts == 3
        assert runtime.next_reconnect_at == 123

    def test_reconnect_attempts_counter_incremented(self):
        config = MCPServerConfig(
            name="test", transport="stdio", command="python",
            reconnect_initial_delay_sec=1,
        )
        manager = MCPToolManager([config])
        runtime = SimpleNamespace(
            config=config, reconnect_attempts=0, next_reconnect_at=None,
        )

        with patch("time.time", return_value=1000):
            asyncio.run(manager._schedule_reconnect(config, runtime))

        assert runtime.reconnect_attempts == 1

    def test_next_reconnect_at_set(self):
        config = MCPServerConfig(
            name="test", transport="stdio", command="python",
            reconnect_initial_delay_sec=5,
        )
        manager = MCPToolManager([config])
        runtime = SimpleNamespace(
            config=config, reconnect_attempts=0, next_reconnect_at=None,
        )

        with patch("time.time", return_value=1000):
            asyncio.run(manager._schedule_reconnect(config, runtime))

        assert runtime.next_reconnect_at == 1005
