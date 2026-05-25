"""Performance tests for MCP server startup latency, tool call latency, and concurrent contention.

Covers:
    MCP-P1: MCP server startup latency (mocked stdio/http transport)
    MCP-P2: Tool call latency — call_tool p95
    MCP-P3: Concurrent tool call resource contention
"""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentnexus.core.config import MCPServerConfig
from agentnexus.tools.mcp_adapter import MCPToolDescriptor, MCPToolManager

# ── Thresholds ──────────────────────────────────────────────────────

CALL_TOOL_P95_MAX_MS = 50
CONCURRENT_8_P95_MAX_MS = 200


def _percentile(data: list[float], p: int) -> float:
    if not data:
        return 0.0
    sorted_data = sorted(data)
    idx = int(len(sorted_data) * p / 100)
    if idx >= len(sorted_data):
        idx = len(sorted_data) - 1
    return sorted_data[idx]


def _p95_ms(stats_data: list[float]) -> float:
    return _percentile(stats_data, 95) * 1000


def _make_config(name: str, transport: str = "stdio", **kwargs) -> MCPServerConfig:
    if transport == "stdio":
        return MCPServerConfig(name=name, transport="stdio", command="python", **kwargs)
    return MCPServerConfig(name=name, transport="streamable_http", url="http://localhost:9999", **kwargs)


def _mock_mcp_sdk():
    """Build mock MCP SDK objects returned by stdio_client / ClientSession."""
    mock_tool = SimpleNamespace(
        name="echo",
        description="Echo input text",
        inputSchema={"type": "object", "properties": {"text": {"type": "string"}}},
    )

    session = AsyncMock()
    session.initialize = AsyncMock()
    session.list_tools = AsyncMock(return_value=SimpleNamespace(tools=[mock_tool]))
    session.list_resources = AsyncMock(return_value=SimpleNamespace(resources=[]))
    session.list_resource_templates = AsyncMock(return_value=SimpleNamespace(resourceTemplates=[]))
    session.list_prompts = AsyncMock(return_value=SimpleNamespace(prompts=[]))
    session.call_tool = AsyncMock(
        return_value=SimpleNamespace(
            content=[SimpleNamespace(text="ok")],
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


def _mcp_patches(transport_cm, session_cm):
    """Return patch context managers targeting the correct mcp module paths."""
    return (
        patch("mcp.client.stdio.stdio_client", return_value=transport_cm),
        patch("mcp.ClientSession", return_value=session_cm),
    )


def _start_mocked_manager(configs: list[MCPServerConfig]) -> MCPToolManager:
    manager = MCPToolManager(configs, startup_timeout=5)
    _session, transport_cm, session_cm = _mock_mcp_sdk()
    p1, p2 = _mcp_patches(transport_cm, session_cm)
    with p1, p2:
        manager.start()
    return manager


# ── MCP-P1: Server startup latency ─────────────────────────────────


class TestMCPStartupLatency:
    """Measure time to start MCPToolManager with mocked transports."""

    def test_startup_single_stdio_server(self, benchmark):
        config = _make_config("s1", "stdio")

        def _run():
            manager = MCPToolManager([config], startup_timeout=5)
            _session, transport_cm, session_cm = _mock_mcp_sdk()
            p1, p2 = _mcp_patches(transport_cm, session_cm)
            with p1, p2:
                manager.start()
            manager.close()
            return manager

        result = benchmark.pedantic(_run, iterations=1, rounds=5)
        assert result is not None

    def test_startup_ten_stdio_servers(self, benchmark):
        configs = [_make_config(f"srv_{i}", "stdio") for i in range(10)]

        def _run():
            manager = MCPToolManager(configs, startup_timeout=5)
            _session, transport_cm, session_cm = _mock_mcp_sdk()
            p1, p2 = _mcp_patches(transport_cm, session_cm)
            with p1, p2:
                manager.start()
            manager.close()
            return manager

        result = benchmark.pedantic(_run, iterations=1, rounds=5)
        assert result is not None

    def test_startup_overhead_scales_linearly(self):
        """Startup time should grow roughly linearly with server count."""
        times = []
        for count in (1, 5, 10):
            configs = [_make_config(f"srv_{i}", "stdio") for i in range(count)]
            manager = MCPToolManager(configs, startup_timeout=5)
            _session, transport_cm, session_cm = _mock_mcp_sdk()
            p1, p2 = _mcp_patches(transport_cm, session_cm)
            start = time.perf_counter()
            with p1, p2:
                manager.start()
            elapsed = time.perf_counter() - start
            manager.close()
            times.append((count, elapsed))
        single = times[0][1]
        ten = times[2][1]
        assert ten < single * 10 * 3, f"Startup did not scale linearly: 1={single:.3f}s, 10={ten:.3f}s"


# ── MCP-P2: Tool call latency (call_tool p95) ──────────────────────


class TestMCPToolCallLatency:
    """Measure call_tool round-trip latency with mocked async dispatch."""

    @pytest.fixture
    def started_manager(self):
        config = _make_config("perf", "stdio")
        manager = _start_mocked_manager([config])
        nm = "mcp_perf__echo"
        manager._tool_descriptors[nm] = MCPToolDescriptor(
            local_name=nm,
            remote_name="echo",
            server_name="perf",
            description="Echo",
            param_schema={"type": "object", "properties": {"text": {"type": "string"}}},
            allowed_agents=["*"],
            risk_level="low",
            require_hitl=False,
            timeout_sec=30,
            rate_limit_per_min=0,
        )
        yield manager
        manager.close()

    def test_call_tool_single_latency(self, started_manager):
        manager = started_manager
        runs = []
        for _ in range(20):
            start = time.perf_counter()
            manager.call_tool("mcp_perf__echo", {"text": "hello"})
            runs.append(time.perf_counter() - start)
        p95 = _percentile(runs, 95) * 1000
        assert p95 < CALL_TOOL_P95_MAX_MS, f"call_tool p95={p95:.1f}ms >= {CALL_TOOL_P95_MAX_MS}ms"

    def test_call_tool_repeated_benchmark(self, benchmark, started_manager):
        def _run():
            return started_manager.call_tool("mcp_perf__echo", {"text": "benchmark"})

        result = benchmark.pedantic(_run, iterations=5, rounds=10)
        assert result is not None
        p95 = _p95_ms(benchmark.stats.stats.data)
        assert p95 < CALL_TOOL_P95_MAX_MS, f"call_tool p95={p95:.1f}ms >= {CALL_TOOL_P95_MAX_MS}ms"


# ── MCP-P3: Concurrent tool call resource contention ───────────────


class TestMCPConcurrentContention:
    """Verify concurrent call_tool does not corrupt state or deadlock."""

    @pytest.fixture
    def concurrent_manager(self):
        config = _make_config("conc", "stdio", max_concurrency_per_server=4)
        manager = _start_mocked_manager([config])
        nm = "mcp_conc__echo"
        manager._tool_descriptors[nm] = MCPToolDescriptor(
            local_name=nm,
            remote_name="echo",
            server_name="conc",
            description="Echo",
            param_schema={"type": "object", "properties": {}},
            allowed_agents=["*"],
            risk_level="low",
            require_hitl=False,
            timeout_sec=30,
            rate_limit_per_min=0,
        )
        yield manager
        manager.close()

    def test_concurrent_call_tool_8_threads(self, benchmark, concurrent_manager):
        manager = concurrent_manager
        errors = []
        latencies = []

        def _call():
            start = time.perf_counter()
            try:
                manager.call_tool("mcp_conc__echo", {})
            except Exception as exc:
                errors.append(exc)
            latencies.append(time.perf_counter() - start)

        def _run():
            latencies.clear()
            errors.clear()
            threads = [threading.Thread(target=_call) for _ in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=30)
            return len(latencies)

        completed = benchmark.pedantic(_run, iterations=1, rounds=5)
        assert errors == [], f"Concurrent calls raised errors: {errors}"
        assert completed == 8
        p95 = _percentile(latencies, 95) * 1000
        assert p95 < CONCURRENT_8_P95_MAX_MS, f"concurrent p95={p95:.1f}ms >= {CONCURRENT_8_P95_MAX_MS}ms"

    def test_concurrent_call_tool_no_state_corruption(self, concurrent_manager):
        manager = concurrent_manager
        results = []
        lock = threading.Lock()

        def _call(idx: int):
            try:
                r = manager.call_tool("mcp_conc__echo", {"idx": idx})
                with lock:
                    results.append(r)
            except Exception:
                with lock:
                    results.append(None)

        threads = [threading.Thread(target=_call, args=(i,)) for i in range(16)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert len(results) == 16
        assert all(r is not None for r in results), "Some concurrent calls failed"
        assert "mcp_conc__echo" in manager._tool_descriptors
