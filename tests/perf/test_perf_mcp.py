"""Performance tests for MCP adapter — manager startup, tool registration, descriptor building."""

from __future__ import annotations

from types import SimpleNamespace

from agentnexus.core.config import MCPServerConfig
from agentnexus.tools.mcp_adapter import MCPToolDescriptor, MCPToolManager
from agentnexus.tools.tool_executor import ToolExecutor

BUILD_DESCRIPTOR_P95_MAX_MS = 50
REGISTER_TOOLS_P95_MAX_MS = 50
STATUS_SNAPSHOT_P95_MAX_MS = 20
MANAGER_CREATE_P95_MAX_MS = 10


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


def _make_mock_tool(name: str):
    return SimpleNamespace(
        name=name,
        description=f"Tool {name} for testing",
        inputSchema={
            "type": "object",
            "properties": {f"param_{name}": {"type": "string"}},
        },
    )


def _make_config(name: str) -> MCPServerConfig:
    return MCPServerConfig(name=name, transport="stdio", command="python")


# ── Manager creation ────────────────────────────────────────────────


def test_mcp_manager_create_empty(benchmark):
    def _run():
        return MCPToolManager([])

    result = benchmark(_run)
    assert result is not None


def test_mcp_manager_create_many_servers(benchmark):
    servers = [_make_config(f"server_{i}") for i in range(100)]

    def _run():
        return MCPToolManager(servers)

    result = benchmark(_run)
    assert len(result._servers) == 100


# ── Status snapshot scaling ─────────────────────────────────────────


def test_mcp_status_snapshot_small(benchmark):
    manager = MCPToolManager([_make_config(f"s{i}") for i in range(10)])
    manager._started = True

    def _run():
        manager.status_snapshot()

    benchmark(_run)


def test_mcp_status_snapshot_large(benchmark):
    manager = MCPToolManager([_make_config(f"s{i}") for i in range(200)])
    manager._started = True
    for i in range(100):
        nm = f"mcp_s{i}__tool"
        manager._server_runtimes[f"s{i}"] = SimpleNamespace(tool_names=[nm])
        manager._tool_descriptors[nm] = MCPToolDescriptor(
            local_name=nm, remote_name="tool", server_name=f"s{i}",
            description="desc", param_schema={"type": "object", "properties": {}},
            allowed_agents=["*"], risk_level="low", require_hitl=False,
            timeout_sec=30, rate_limit_per_min=0,
        )

    def _run():
        manager.status_snapshot()

    benchmark(_run)
    p95 = _p95_ms(benchmark.stats.stats.data)
    assert p95 < STATUS_SNAPSHOT_P95_MAX_MS, f"p95={p95:.1f}ms >= {STATUS_SNAPSHOT_P95_MAX_MS}ms"


# ── Build descriptor ────────────────────────────────────────────────


def test_mcp_build_descriptor_single(benchmark):
    manager = MCPToolManager([])
    config = _make_config("perf")
    tool = _make_mock_tool("search")

    def _run():
        return manager._build_descriptor(config, tool)

    descriptor = benchmark(_run)
    assert descriptor is not None


def test_mcp_build_descriptor_many(benchmark):
    manager = MCPToolManager([])
    config = _make_config("perf")
    tools = [_make_mock_tool(f"tool_{i}") for i in range(100)]

    def _run():
        result = []
        for tool in tools:
            d = manager._build_descriptor(config, tool)
            if d:
                result.append(d)
        return result

    descriptors = benchmark(_run)
    assert len(descriptors) == 100
    p95 = _p95_ms(benchmark.stats.stats.data)
    assert p95 < BUILD_DESCRIPTOR_P95_MAX_MS, f"p95={p95:.1f}ms >= {BUILD_DESCRIPTOR_P95_MAX_MS}ms"


# ── Register tools ──────────────────────────────────────────────────


def test_mcp_register_tools_empty(benchmark):
    manager = MCPToolManager([])
    executor = ToolExecutor()

    def _run():
        manager.register_tools(executor)

    benchmark(_run)


def test_mcp_register_tools_many(benchmark):
    manager = MCPToolManager([])
    for i in range(200):
        nm = f"mcp_perf__tool_{i}"
        manager._tool_descriptors[nm] = MCPToolDescriptor(
            local_name=nm, remote_name=f"tool_{i}", server_name="perf",
            description="desc", param_schema={"type": "object", "properties": {}},
            allowed_agents=["*"], risk_level="low", require_hitl=False,
            timeout_sec=30, rate_limit_per_min=0,
        )

    def _run():
        executor = ToolExecutor()
        return manager.register_tools(executor)

    registered = benchmark(_run)
    assert len(registered) == 200
    p95 = _p95_ms(benchmark.stats.stats.data)
    assert p95 < REGISTER_TOOLS_P95_MAX_MS, f"p95={p95:.1f}ms >= {REGISTER_TOOLS_P95_MAX_MS}ms"


def test_mcp_register_tools_with_filter(benchmark):
    manager = MCPToolManager([])
    for i in range(200):
        nm = f"mcp_perf__tool_{i}"
        manager._tool_descriptors[nm] = MCPToolDescriptor(
            local_name=nm, remote_name=f"tool_{i}", server_name="perf",
            description="desc", param_schema={"type": "object", "properties": {}},
            allowed_agents=["*"], risk_level="low", require_hitl=False,
            timeout_sec=30, rate_limit_per_min=0,
        )
    include = {f"mcp_perf__tool_{i}" for i in range(50)}

    def _run():
        executor = ToolExecutor()
        return manager.register_tools(executor, include_tools=include)

    registered = benchmark(_run)
    assert len(registered) == 50


# ── Tool name sanitization ──────────────────────────────────────────


def test_mcp_sanitize_name_many(benchmark):
    from agentnexus.tools.mcp_adapter import _sanitize_name

    names = [f"Hello World! {i} @#$%" for i in range(100)]

    def _run():
        return [_sanitize_name(n) for n in names]

    results = benchmark(_run)
    assert len(results) == 100


# ── Normalize tool result ───────────────────────────────────────────


def test_mcp_normalize_result_many(benchmark):
    from agentnexus.tools.mcp_adapter import _normalize_tool_result

    results = []
    for i in range(200):
        results.append(
            SimpleNamespace(
                content=[SimpleNamespace(text=f"line {i}")],
                structuredContent=None,
                isError=False,
            )
        )

    def _run():
        return [_normalize_tool_result(r) for r in results]

    texts = benchmark(_run)
    assert len(texts) == 200


# ── Ensure unique name ──────────────────────────────────────────────


def test_mcp_ensure_unique_name_many_collisions(benchmark):
    manager = MCPToolManager([])
    for i in range(100):
        nm = f"collision_{i}" if i > 0 else "collision"
        manager._tool_descriptors[nm] = MCPToolDescriptor(
            local_name=nm, remote_name="x", server_name="x",
            description="desc", param_schema={},
            allowed_agents=["*"], risk_level="low", require_hitl=False,
            timeout_sec=30, rate_limit_per_min=0,
        )

    def _run():
        return manager._ensure_unique_name("collision")

    name = benchmark(_run)
    assert name == "collision_100"
