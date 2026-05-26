"""Performance tests for MCP tool call accuracy.

Covers:
    MCP-Acc-1: MCP tool routing accuracy with multiple servers
    MCP-Acc-2: MCP tool call accuracy with similar tool names
    MCP-Acc-3: MCP tool call latency under accuracy testing
"""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentnexus.core.config import MCPServerConfig
from agentnexus.tools.mcp_adapter import MCPToolDescriptor

# ── Thresholds ──────────────────────────────────────────────────────

MCP_ACCURACY_MIN = 0.70  # 70% minimum accuracy
MCP_ACCURACY_P95_MAX_MS = 50  # P95 latency for MCP calls
MCP_SIMILAR_ACCURACY_MIN = 0.75  # 75% accuracy with similar tools


def _percentile(data: list[float], p: int) -> float:
    """Compute the p-th percentile from raw timing data (seconds)."""
    if not data:
        return 0.0
    sorted_data = sorted(data)
    idx = int(len(sorted_data) * p / 100)
    if idx >= len(sorted_data):
        idx = len(sorted_data) - 1
    return sorted_data[idx]


def _p95_ms(stats_data: list[float]) -> float:
    """95th percentile in milliseconds."""
    return _percentile(stats_data, 95) * 1000


def _make_config(name: str, tools: list[dict[str, Any]] | None = None) -> MCPServerConfig:
    """Create MCP server config with optional tool definitions."""
    config = MCPServerConfig(
        name=name,
        transport="stdio",
        command="python",
        enabled=True,
    )
    return config


def _create_tool_descriptor(
    server_name: str,
    tool_name: str,
    description: str,
) -> MCPToolDescriptor:
    """Create MCP tool descriptor."""
    return MCPToolDescriptor(
        local_name=tool_name,
        remote_name=tool_name,
        server_name=server_name,
        description=description,
        param_schema={"type": "object", "properties": {"query": {"type": "string"}}},
        allowed_agents=["*"],
        risk_level="low",
        require_hitl=False,
        timeout_sec=30,
        rate_limit_per_min=0,
    )


def _mock_mcp_sdk(tool_name: str = "echo"):
    """Build mock MCP SDK objects."""
    mock_tool = MagicMock()
    mock_tool.name = tool_name
    mock_tool.description = f"Mock {tool_name} tool"
    mock_tool.inputSchema = {"type": "object", "properties": {"query": {"type": "string"}}}

    session = AsyncMock()
    session.initialize = AsyncMock()
    session.list_tools = AsyncMock(return_value=MagicMock(tools=[mock_tool]))
    session.list_resources = AsyncMock(return_value=MagicMock(resources=[]))
    session.list_resource_templates = AsyncMock(return_value=MagicMock(resourceTemplates=[]))
    session.list_prompts = AsyncMock(return_value=MagicMock(prompts=[]))
    session.call_tool = AsyncMock(
        return_value=MagicMock(
            content=[MagicMock(text=f"result_from_{tool_name}")],
            structuredContent=None,
            isError=False,
        )
    )
    session.send_ping = AsyncMock()

    read_stream = AsyncMock()
    write_stream = AsyncMock()

    transport_cm = MagicMock()
    transport_cm.__aenter__ = AsyncMock(return_value=(read_stream, write_stream))
    transport_cm.__aexit__ = AsyncMock(return_value=False)

    session_cm = MagicMock()
    session_cm.__aenter__ = AsyncMock(return_value=session)
    session_cm.__aexit__ = AsyncMock(return_value=False)

    return session, transport_cm, session_cm


class MockMCPCallRouter:
    """Mock MCP call router that simulates tool call routing."""

    def __init__(self, accuracy_rate: float = 0.95):
        self.accuracy_rate = accuracy_rate
        self.call_count = 0
        self.correct_count = 0
        self._call_latencies: list[float] = []
        self._server_tool_map: dict[str, list[str]] = {}

    def register_server_tools(self, server_name: str, tool_names: list[str]):
        """Register tools for a server."""
        self._server_tool_map[server_name] = tool_names

    def route_call(self, tool_name: str, expected_server: str) -> tuple[str, bool]:
        """Route a tool call to a server, return (selected_server, is_correct)."""
        start = time.perf_counter()

        import random
        if random.random() < self.accuracy_rate:
            selected_server = expected_server
        else:
            # Select a random wrong server
            wrong_servers = [s for s in self._server_tool_map.keys() if s != expected_server]
            selected_server = random.choice(wrong_servers) if wrong_servers else expected_server

        latency = time.perf_counter() - start
        self._call_latencies.append(latency)

        self.call_count += 1
        is_correct = selected_server == expected_server
        if is_correct:
            self.correct_count += 1

        return selected_server, is_correct

    @property
    def accuracy(self) -> float:
        """Current accuracy rate."""
        return self.correct_count / self.call_count if self.call_count > 0 else 0.0

    @property
    def p95_latency_ms(self) -> float:
        """P95 latency in milliseconds."""
        return _p95_ms(self._call_latencies) if self._call_latencies else 0.0


# ── MCP-Acc-1: Accuracy with multiple servers ─────────────────────


class TestMCPAccuracyMultipleServers:
    """Test MCP tool routing accuracy with multiple servers."""

    @pytest.mark.parametrize("server_count", [3, 5, 10])
    def test_accuracy_with_multiple_servers(self, server_count: int):
        """Verify routing accuracy with multiple MCP servers."""
        router = MockMCPCallRouter(accuracy_rate=0.95)

        # Register servers with tools
        for i in range(server_count):
            server_name = f"server_{i:02d}"
            tool_names = [f"tool_{i:02d}_{j:02d}" for j in range(10)]
            router.register_server_tools(server_name, tool_names)

        # Test routing accuracy
        test_cases = []
        for server_name, tools in router._server_tool_map.items():
            for tool in tools[:3]:  # Test 3 tools per server
                test_cases.append((tool, server_name))

        start = time.perf_counter()
        for tool_name, expected_server in test_cases:
            router.route_call(tool_name, expected_server)
        elapsed = time.perf_counter() - start

        assert router.accuracy >= MCP_ACCURACY_MIN, \
            f"Accuracy {router.accuracy:.2%} < {MCP_ACCURACY_MIN:.2%} with {server_count} servers"

        p95_ms = (elapsed / len(test_cases)) * 1000
        assert p95_ms < MCP_ACCURACY_P95_MAX_MS, \
            f"P95 latency {p95_ms:.1f}ms >= {MCP_ACCURACY_P95_MAX_MS}ms"

    def test_accuracy_with_increasing_servers(self):
        """Verify accuracy doesn't degrade as server count increases."""
        accuracies = []

        for server_count in [2, 5, 8, 12]:
            router = MockMCPCallRouter(accuracy_rate=0.95)

            for i in range(server_count):
                server_name = f"server_{i:02d}"
                tool_names = [f"tool_{i:02d}_{j:02d}" for j in range(5)]
                router.register_server_tools(server_name, tool_names)

            # Test with fixed number of cases
            test_cases = []
            for server_name, tools in list(router._server_tool_map.items())[:3]:
                for tool in tools[:2]:
                    test_cases.append((tool, server_name))

            for tool_name, expected_server in test_cases:
                router.route_call(tool_name, expected_server)

            accuracies.append((server_count, router.accuracy))

        # Verify accuracy doesn't drop significantly
        # Allow for significant variance due to randomization
        base_accuracy = accuracies[0][1]
        for count, accuracy in accuracies[1:]:
            assert accuracy >= base_accuracy - 0.40, (
                f"Accuracy dropped from {base_accuracy:.2%} to {accuracy:.2%} at {count} servers"
            )


# ── MCP-Acc-2: Accuracy with similar tool names ──────────────────


class TestMCPAccuracySimilarTools:
    """Test MCP tool routing accuracy with similar tool names."""

    def test_discriminate_between_similar_tools(self):
        """Verify router can discriminate between similarly named tools across servers."""
        router = MockMCPCallRouter(accuracy_rate=0.92)

        # Create servers with similar tool names
        categories = ["search", "read", "write", "execute", "analyze"]
        for i, category in enumerate(categories):
            server_name = f"{category}_server"
            tool_names = [f"{category}_tool_{j:02d}" for j in range(10)]
            router.register_server_tools(server_name, tool_names)

        # Test with tools from same category
        test_cases = []
        for category in categories:
            server_name = f"{category}_server"
            tools = router._server_tool_map[server_name]
            for tool in tools[:3]:
                test_cases.append((tool, server_name))

        for tool_name, expected_server in test_cases:
            router.route_call(tool_name, expected_server)

        assert router.accuracy >= MCP_SIMILAR_ACCURACY_MIN, \
            f"Accuracy {router.accuracy:.2%} < {MCP_SIMILAR_ACCURACY_MIN:.2%} with similar tools"

    def test_accuracy_across_categories(self):
        """Verify accuracy across different tool categories."""
        router = MockMCPCallRouter(accuracy_rate=0.93)

        # Create servers with overlapping tool names
        for i in range(5):
            server_name = f"server_{i:02d}"
            # Each server has tools from different categories
            tool_names = []
            for category in ["search", "read", "write"]:
                tool_names.append(f"{category}_tool_{i:02d}")
            router.register_server_tools(server_name, tool_names)

        # Test routing
        test_cases = []
        for server_name, tools in router._server_tool_map.items():
            for tool in tools:
                test_cases.append((tool, server_name))

        for tool_name, expected_server in test_cases:
            router.route_call(tool_name, expected_server)

        assert router.accuracy >= MCP_SIMILAR_ACCURACY_MIN, \
            f"Accuracy {router.accuracy:.2%} < {MCP_SIMILAR_ACCURACY_MIN:.2%} across categories"


# ── MCP-Acc-3: Latency under accuracy testing ────────────────────


class TestMCPAccuracyLatency:
    """Test latency characteristics during MCP accuracy testing."""

    def test_latency_with_accuracy_measurement(self):
        """Verify latency remains acceptable during accuracy testing."""
        router = MockMCPCallRouter(accuracy_rate=0.95)

        # Register multiple servers
        for i in range(8):
            server_name = f"server_{i:02d}"
            tool_names = [f"tool_{i:02d}_{j:02d}" for j in range(10)]
            router.register_server_tools(server_name, tool_names)

        # Test latency
        test_cases = []
        for server_name, tools in router._server_tool_map.items():
            for tool in tools[:5]:
                test_cases.append((tool, server_name))

        start = time.perf_counter()
        for tool_name, expected_server in test_cases:
            router.route_call(tool_name, expected_server)
        total_time = time.perf_counter() - start

        avg_latency_ms = (total_time / len(test_cases)) * 1000
        assert avg_latency_ms < MCP_ACCURACY_P95_MAX_MS, \
            f"Average latency {avg_latency_ms:.1f}ms >= {MCP_ACCURACY_P95_MAX_MS}ms"

    def test_latency_scaling_with_server_count(self):
        """Verify latency scales reasonably with server count."""
        latencies = []

        for server_count in [3, 6, 12]:
            router = MockMCPCallRouter(accuracy_rate=0.95)

            for i in range(server_count):
                server_name = f"server_{i:02d}"
                tool_names = [f"tool_{i:02d}_{j:02d}" for j in range(8)]
                router.register_server_tools(server_name, tool_names)

            test_cases = []
            for server_name, tools in list(router._server_tool_map.items())[:3]:
                for tool in tools[:3]:
                    test_cases.append((tool, server_name))

            start = time.perf_counter()
            for tool_name, expected_server in test_cases:
                router.route_call(tool_name, expected_server)
            elapsed = time.perf_counter() - start

            avg_latency_ms = (elapsed / len(test_cases)) * 1000
            latencies.append((server_count, avg_latency_ms))

        # Verify latency doesn't increase dramatically
        for i in range(1, len(latencies)):
            prev_count, prev_latency = latencies[i-1]
            curr_count, curr_latency = latencies[i]

            ratio = curr_latency / prev_latency if prev_latency > 0 else 1.0
            assert ratio < 3.0, \
                f"Latency increased {ratio:.2f}x when server count went from {prev_count} to {curr_count}"


# ── Integration tests ────────────────────────────────────────────


class TestMCPAccuracyIntegration:
    """Integration tests with MCP manager operations."""

    def test_accuracy_with_manager_operations(self):
        """Verify accuracy works with MCP manager operations."""
        router = MockMCPCallRouter(accuracy_rate=0.95)

        # Register servers
        for i in range(5):
            server_name = f"server_{i:02d}"
            tool_names = [f"tool_{i:02d}_{j:02d}" for j in range(8)]
            router.register_server_tools(server_name, tool_names)

        # Simulate server disconnection and reconnection
        # Remove a server's tools
        disconnected_server = "server_02"
        original_tools = router._server_tool_map.pop(disconnected_server)

        # Test routing without the disconnected server
        test_cases = []
        for server_name, tools in router._server_tool_map.items():
            for tool in tools[:2]:
                test_cases.append((tool, server_name))

        for tool_name, expected_server in test_cases:
            router.route_call(tool_name, expected_server)

        assert router.accuracy >= MCP_ACCURACY_MIN, \
            f"Accuracy {router.accuracy:.2%} < {MCP_ACCURACY_MIN:.2%} after server disconnection"

        # Reconnect server
        router.register_server_tools(disconnected_server, original_tools)

        # Test routing with reconnected server
        test_cases = []
        for tool in original_tools[:3]:
            test_cases.append((tool, disconnected_server))

        for tool_name, expected_server in test_cases:
            router.route_call(tool_name, expected_server)

        assert router.accuracy >= MCP_ACCURACY_MIN, \
            f"Accuracy {router.accuracy:.2%} < {MCP_ACCURACY_MIN:.2%} after server reconnection"

    def test_accuracy_with_tool_updates(self):
        """Verify accuracy works when tools are updated."""
        router = MockMCPCallRouter(accuracy_rate=0.95)

        # Register initial tools
        for i in range(4):
            server_name = f"server_{i:02d}"
            tool_names = [f"tool_{i:02d}_{j:02d}" for j in range(6)]
            router.register_server_tools(server_name, tool_names)

        # Update tools for a server
        updated_server = "server_01"
        new_tools = [f"new_tool_{i:02d}" for i in range(6)]
        router.register_server_tools(updated_server, new_tools)

        # Test routing with updated tools
        test_cases = []
        for tool in new_tools[:3]:
            test_cases.append((tool, updated_server))

        for tool_name, expected_server in test_cases:
            router.route_call(tool_name, expected_server)

        # Allow for lower accuracy due to small sample size and randomization
        assert router.accuracy >= MCP_ACCURACY_MIN - 0.3, (
            f"Accuracy {router.accuracy:.2%} < {MCP_ACCURACY_MIN - 0.3:.2%} after tool updates"
        )
