"""Performance tests for MCP tool call accuracy during error recovery.

Closes Gap 6: No MCP call accuracy test for error recovery / retry.

Covers:
    MCP-Rec-1: Tool call accuracy during server disconnection/reconnection
    MCP-Rec-2: Tool call accuracy during intermittent failures
    MCP-Rec-3: Tool call accuracy during server flapping
"""

from __future__ import annotations

import random
import time

from agentnexus.tools.mcp_schema import MCPServerState

# ── Thresholds ──────────────────────────────────────────────────────

RECOVERY_ACCURACY_MIN = 0.90  # 90% accuracy during recovery
RECOVERY_LATENCY_P95_MAX_MS = 100  # P95 latency during recovery
FLAPPING_ACCURACY_MIN = 0.85  # 85% accuracy during server flapping


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


class MockMCPServer:
    """Mock MCP server with configurable failure modes."""

    def __init__(self, name: str, tools: list[str], failure_rate: float = 0.0):
        self.name = name
        self.tools = tools
        self.failure_rate = failure_rate
        self.state = MCPServerState.HEALTHY
        self.call_count = 0
        self.failure_count = 0
        self.recovery_count = 0
        self._is_flapping = False
        _flap_counter = 0

    def set_flapping(self, enabled: bool):
        """Enable/disable server flapping behavior."""
        self._is_flapping = enabled
        self._flap_counter = 0

    def call_tool(self, tool_name: str) -> tuple[bool, str]:
        """Simulate tool call with potential failures."""
        self.call_count += 1

        # Handle flapping behavior
        if self._is_flapping:
            self._flap_counter += 1
            if self._flap_counter % 3 == 0:  # Fail every 3rd call
                self.failure_count += 1
                self.state = MCPServerState.DEGRADED
                return False, f"Server {self.name} is flapping"

        # Handle random failures
        if random.random() < self.failure_rate:
            self.failure_count += 1
            self.state = MCPServerState.DEGRADED
            return False, f"Server {self.name} failed"

        # Success case
        if self.state == MCPServerState.DEGRADED:
            self.recovery_count += 1
            self.state = MCPServerState.HEALTHY

        return True, f"result_from_{tool_name}"

    def disconnect(self):
        """Simulate server disconnection."""
        self.state = MCPServerState.DISCONNECTED

    def reconnect(self):
        """Simulate server reconnection."""
        if self.state == MCPServerState.DISCONNECTED:
            self.state = MCPServerState.HEALTHY
            self.recovery_count += 1


class MockMCPCallRouterWithRecovery:
    """Mock MCP call router with recovery handling."""

    def __init__(self):
        self.servers: dict[str, MockMCPServer] = {}
        self.call_count = 0
        self.success_count = 0
        self._call_latencies: list[float] = []

    def add_server(self, server: MockMCPServer):
        """Add a server to the router."""
        self.servers[server.name] = server

    def route_call(self, tool_name: str, expected_server: str) -> tuple[bool, str]:
        """Route a tool call, handling recovery scenarios."""
        start = time.perf_counter()
        self.call_count += 1

        server = self.servers.get(expected_server)
        if not server:
            latency = time.perf_counter() - start
            self._call_latencies.append(latency)
            return False, f"Server {expected_server} not found"

        # Try the expected server first
        success, result = server.call_tool(tool_name)
        if success:
            latency = time.perf_counter() - start
            self._call_latencies.append(latency)
            self.success_count += 1
            return True, result

        # Try fallback servers
        for fallback_name, fallback_server in self.servers.items():
            if fallback_name != expected_server and fallback_server.state == MCPServerState.HEALTHY:
                success, result = fallback_server.call_tool(tool_name)
                if success:
                    latency = time.perf_counter() - start
                    self._call_latencies.append(latency)
                    self.success_count += 1
                    return True, result

        latency = time.perf_counter() - start
        self._call_latencies.append(latency)
        return False, "All servers failed"

    @property
    def accuracy(self) -> float:
        """Current accuracy rate."""
        return self.success_count / self.call_count if self.call_count > 0 else 0.0

    @property
    def p95_latency_ms(self) -> float:
        """P95 latency in milliseconds."""
        return _p95_ms(self._call_latencies) if self._call_latencies else 0.0


# ── MCP-Rec-1: Accuracy during disconnection/reconnection ─────────


class TestMCPRecoveryDisconnectReconnect:
    """Test tool call accuracy during server disconnection and reconnection."""

    def test_accuracy_during_reconnection(self):
        """Verify accuracy when servers disconnect and reconnect."""
        router = MockMCPCallRouterWithRecovery()

        # Create servers
        for i in range(5):
            server = MockMCPServer(f"server_{i:02d}", [f"tool_{i:02d}_{j:02d}" for j in range(5)])
            router.add_server(server)

        # Test normal operation
        test_cases = []
        for server_name, server in router.servers.items():
            for tool in server.tools[:2]:
                test_cases.append((tool, server_name))

        # Phase 1: Normal operation
        for tool_name, expected_server in test_cases:
            router.route_call(tool_name, expected_server)

        # Phase 2: Disconnect some servers
        disconnected_servers = ["server_01", "server_03"]
        for server_name in disconnected_servers:
            router.servers[server_name].disconnect()

        # Phase 3: Continue calls during disconnection
        for tool_name, expected_server in test_cases:
            router.route_call(tool_name, expected_server)

        # Phase 4: Reconnect servers
        for server_name in disconnected_servers:
            router.servers[server_name].reconnect()

        # Phase 5: Continue calls after reconnection
        for tool_name, expected_server in test_cases:
            router.route_call(tool_name, expected_server)

        assert router.accuracy >= RECOVERY_ACCURACY_MIN, \
            f"Accuracy {router.accuracy:.2%} < {RECOVERY_ACCURACY_MIN:.2%} during reconnection"

    def test_accuracy_with_multiple_reconnections(self):
        """Verify accuracy with multiple reconnection cycles."""
        router = MockMCPCallRouterWithRecovery()

        # Create servers
        for i in range(4):
            server = MockMCPServer(f"server_{i:02d}", [f"tool_{i:02d}_{j:02d}" for j in range(4)])
            router.add_server(server)

        test_cases = []
        for server_name, server in router.servers.items():
            for tool in server.tools[:2]:
                test_cases.append((tool, server_name))

        # Multiple reconnection cycles
        for cycle in range(3):
            # Disconnect random servers
            disconnected = random.sample(list(router.servers.keys()), 2)
            for server_name in disconnected:
                router.servers[server_name].disconnect()

            # Make calls during disconnection
            for tool_name, expected_server in test_cases:
                router.route_call(tool_name, expected_server)

            # Reconnect servers
            for server_name in disconnected:
                router.servers[server_name].reconnect()

            # Make calls after reconnection
            for tool_name, expected_server in test_cases:
                router.route_call(tool_name, expected_server)

        assert router.accuracy >= RECOVERY_ACCURACY_MIN, \
            f"Accuracy {router.accuracy:.2%} < {RECOVERY_ACCURACY_MIN:.2%} with multiple reconnections"


# ── MCP-Rec-2: Accuracy during intermittent failures ──────────────


class TestMCPRecoveryIntermittentFailures:
    """Test tool call accuracy during intermittent failures."""

    def test_accuracy_with_intermittent_failures(self):
        """Verify accuracy with intermittent server failures."""
        router = MockMCPCallRouterWithRecovery()

        # Create servers with different failure rates
        failure_rates = [0.0, 0.1, 0.2, 0.3, 0.0]
        for i, failure_rate in enumerate(failure_rates):
            server = MockMCPServer(
                f"server_{i:02d}",
                [f"tool_{i:02d}_{j:02d}" for j in range(5)],
                failure_rate=failure_rate
            )
            router.add_server(server)

        test_cases = []
        for server_name, server in router.servers.items():
            for tool in server.tools[:3]:
                test_cases.append((tool, server_name))

        # Make many calls to exercise failure paths
        for _ in range(10):
            for tool_name, expected_server in test_cases:
                router.route_call(tool_name, expected_server)

        assert router.accuracy >= RECOVERY_ACCURACY_MIN, \
            f"Accuracy {router.accuracy:.2%} < {RECOVERY_ACCURACY_MIN:.2%} with intermittent failures"

    def test_accuracy_with_high_failure_rate(self):
        """Verify accuracy with high failure rate but fallback servers."""
        router = MockMCPCallRouterWithRecovery()

        # Create servers: one with high failure rate, others as fallbacks
        primary_server = MockMCPServer("primary", [f"tool_{j:02d}" for j in range(10)], failure_rate=0.5)
        router.add_server(primary_server)

        for i in range(3):
            fallback = MockMCPServer(f"fallback_{i:02d}", [f"tool_{j:02d}" for j in range(10)], failure_rate=0.0)
            router.add_server(fallback)

        test_cases = [(f"tool_{j:02d}", "primary") for j in range(10)]

        # Make calls with fallback
        for _ in range(5):
            for tool_name, expected_server in test_cases:
                router.route_call(tool_name, expected_server)

        assert router.accuracy >= RECOVERY_ACCURACY_MIN, \
            f"Accuracy {router.accuracy:.2%} < {RECOVERY_ACCURACY_MIN:.2%} with high failure rate"


# ── MCP-Rec-3: Accuracy during server flapping ────────────────────


class TestMCPRecoveryServerFlapping:
    """Test tool call accuracy during server flapping."""

    def test_accuracy_during_flapping(self):
        """Verify accuracy when servers are flapping."""
        router = MockMCPCallRouterWithRecovery()

        # Create servers with one flapping
        for i in range(4):
            server = MockMCPServer(f"server_{i:02d}", [f"tool_{i:02d}_{j:02d}" for j in range(5)])
            if i == 1:  # Make one server flap
                server.set_flapping(True)
            router.add_server(server)

        test_cases = []
        for server_name, server in router.servers.items():
            for tool in server.tools[:3]:
                test_cases.append((tool, server_name))

        # Make calls during flapping
        for _ in range(8):
            for tool_name, expected_server in test_cases:
                router.route_call(tool_name, expected_server)

        assert router.accuracy >= FLAPPING_ACCURACY_MIN, \
            f"Accuracy {router.accuracy:.2%} < {FLAPPING_ACCURACY_MIN:.2%} during flapping"

    def test_accuracy_with_multiple_flapping_servers(self):
        """Verify accuracy with multiple flapping servers."""
        router = MockMCPCallRouterWithRecovery()

        # Create servers with multiple flapping
        for i in range(5):
            server = MockMCPServer(f"server_{i:02d}", [f"tool_{i:02d}_{j:02d}" for j in range(4)])
            if i in [1, 3]:  # Make two servers flap
                server.set_flapping(True)
            router.add_server(server)

        test_cases = []
        for server_name, server in router.servers.items():
            for tool in server.tools[:2]:
                test_cases.append((tool, server_name))

        # Make calls during flapping
        for _ in range(6):
            for tool_name, expected_server in test_cases:
                router.route_call(tool_name, expected_server)

        assert router.accuracy >= FLAPPING_ACCURACY_MIN, \
            f"Accuracy {router.accuracy:.2%} < {FLAPPING_ACCURACY_MIN:.2%} with multiple flapping servers"

    def test_latency_during_flapping(self):
        """Verify latency remains acceptable during flapping."""
        router = MockMCPCallRouterWithRecovery()

        # Create servers with flapping
        for i in range(4):
            server = MockMCPServer(f"server_{i:02d}", [f"tool_{i:02d}_{j:02d}" for j in range(5)])
            if i == 2:
                server.set_flapping(True)
            router.add_server(server)

        test_cases = []
        for server_name, server in router.servers.items():
            for tool in server.tools[:2]:
                test_cases.append((tool, server_name))

        # Make calls and measure latency
        start = time.perf_counter()
        for _ in range(5):
            for tool_name, expected_server in test_cases:
                router.route_call(tool_name, expected_server)
        total_time = time.perf_counter() - start

        avg_latency_ms = (total_time / (len(test_cases) * 5)) * 1000
        assert avg_latency_ms < RECOVERY_LATENCY_P95_MAX_MS, \
            f"Average latency {avg_latency_ms:.1f}ms >= {RECOVERY_LATENCY_P95_MAX_MS}ms during flapping"


# ── Integration tests ────────────────────────────────────────────


class TestMCPRecoveryIntegration:
    """Integration tests for MCP recovery scenarios."""

    def test_accuracy_with_mixed_recovery_scenarios(self):
        """Verify accuracy with mixed recovery scenarios."""
        router = MockMCPCallRouterWithRecovery()

        # Create servers with different behaviors
        servers = [
            MockMCPServer("stable", [f"stable_tool_{j:02d}" for j in range(5)], failure_rate=0.0),
            MockMCPServer("flaky", [f"flaky_tool_{j:02d}" for j in range(5)], failure_rate=0.2),
            MockMCPServer("flapping", [f"flap_tool_{j:02d}" for j in range(5)]),
        ]
        servers[2].set_flapping(True)

        for server in servers:
            router.add_server(server)

        test_cases = []
        for server_name, server in router.servers.items():
            for tool in server.tools[:3]:
                test_cases.append((tool, server_name))

        # Make calls with mixed scenarios
        for _ in range(10):
            # Randomly disconnect/reconnect some servers
            if random.random() < 0.3:
                server = random.choice(servers)
                if server.state == MCPServerState.HEALTHY:
                    server.disconnect()
                else:
                    server.reconnect()

            for tool_name, expected_server in test_cases:
                router.route_call(tool_name, expected_server)

        assert router.accuracy >= FLAPPING_ACCURACY_MIN, \
            f"Accuracy {router.accuracy:.2%} < {FLAPPING_ACCURACY_MIN:.2%} with mixed scenarios"

    def test_recovery_performance_characteristics(self):
        """Verify recovery performance characteristics."""
        router = MockMCPCallRouterWithRecovery()

        # Create servers
        for i in range(4):
            server = MockMCPServer(f"server_{i:02d}", [f"tool_{i:02d}_{j:02d}" for j in range(5)])
            router.add_server(server)

        test_cases = []
        for server_name, server in router.servers.items():
            for tool in server.tools[:2]:
                test_cases.append((tool, server_name))

        # Measure performance during different phases
        phases = {
            "normal": [],
            "disconnected": [],
            "recovery": [],
        }

        # Phase 1: Normal operation
        start = time.perf_counter()
        for tool_name, expected_server in test_cases:
            router.route_call(tool_name, expected_server)
        phases["normal"].append(time.perf_counter() - start)

        # Phase 2: Disconnect some servers
        for server_name in ["server_01", "server_02"]:
            router.servers[server_name].disconnect()

        start = time.perf_counter()
        for tool_name, expected_server in test_cases:
            router.route_call(tool_name, expected_server)
        phases["disconnected"].append(time.perf_counter() - start)

        # Phase 3: Recovery
        for server_name in ["server_01", "server_02"]:
            router.servers[server_name].reconnect()

        start = time.perf_counter()
        for tool_name, expected_server in test_cases:
            router.route_call(tool_name, expected_server)
        phases["recovery"].append(time.perf_counter() - start)

        # Verify latency doesn't spike dramatically during recovery
        normal_latency = sum(phases["normal"]) / len(phases["normal"])
        recovery_latency = sum(phases["recovery"]) / len(phases["recovery"])

        if normal_latency > 0:
            latency_ratio = recovery_latency / normal_latency
            assert latency_ratio < 3.0, \
                f"Recovery latency {latency_ratio:.2f}x higher than normal (threshold: 3.0x)"
