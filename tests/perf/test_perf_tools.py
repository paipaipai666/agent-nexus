"""Performance tests for the tools layer — registry dispatch, shell exec."""

from __future__ import annotations

from typing import Any

import pytest

from agentnexus.tools.registry import ToolMeta, ToolRegistry

REGISTRY_INVOKE_P95_MAX_MS = 200
REGISTRY_INVOKE_LARGE_P95_MAX_MS = 200
SHELL_STARTUP_P95_MAX_MS = 200
GREP_SEARCH_P95_MAX_MS = 200


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


def _make_handler(n: int) -> Any:
    return lambda x, _n=n: _n


def _populate(registry: ToolRegistry, count: int) -> None:
    for i in range(count):
        meta = ToolMeta(
            name=f"tool_{i}",
            description=f"Test tool {i}",
            param_schema={"type": "object", "properties": {"x": {"type": "string"}}},
        )
        registry.register(meta, _make_handler(i))


# ── Registry dispatch ───────────────────────────────────────────


def test_registry_dispatch_small(benchmark):
    r = ToolRegistry()
    _populate(r, 10)

    def _invoke():
        for i in range(10):
            r.invoke(f"tool_{i}", {"x": "test"})

    benchmark(_invoke)
    p95 = _p95_ms(benchmark.stats.stats.data)
    assert p95 < REGISTRY_INVOKE_P95_MAX_MS, \
        f"p95={p95:.1f}ms >= {REGISTRY_INVOKE_P95_MAX_MS}ms"


@pytest.mark.parametrize("tool_count", [100, 500])
def test_registry_dispatch_large(benchmark, tool_count):
    r = ToolRegistry()
    _populate(r, tool_count)

    def _invoke():
        limit = min(100, tool_count)
        for i in range(limit):
            r.invoke(f"tool_{i}", {"x": "test"})

    benchmark(_invoke)
    p95 = _p95_ms(benchmark.stats.stats.data)
    assert p95 < REGISTRY_INVOKE_LARGE_P95_MAX_MS, \
        f"p95={p95:.1f}ms >= {REGISTRY_INVOKE_LARGE_P95_MAX_MS}ms"


def test_registry_list_tools(benchmark):
    r = ToolRegistry()
    for i in range(100):
        agent = "agent_a" if i % 2 == 0 else "agent_b"
        meta = ToolMeta(
            name=f"tool_{i}",
            description=f"tool {i}",
            param_schema={"type": "object", "properties": {"x": {"type": "string"}}},
            allowed_agents=[agent],
        )
        r.register(meta, _make_handler(i))

    benchmark(r.get_available_tools, "agent_a")


def test_registry_to_openai_tools(benchmark):
    r = ToolRegistry()
    _populate(r, 100)

    benchmark(r.to_openai_tools)


# ── Shell execution ─────────────────────────────────────────────


def test_shell_exec_simple(benchmark):
    from agentnexus.tools.shell import shell_exec

    benchmark(shell_exec, command="echo hello", timeout=5)
    p95 = _p95_ms(benchmark.stats.stats.data)
    assert p95 < SHELL_STARTUP_P95_MAX_MS, \
        f"p95={p95:.1f}ms >= {SHELL_STARTUP_P95_MAX_MS}ms"


# ── Grep search ──────────────────────────────────────────────────


def test_grep_search(benchmark, perf_env, monkeypatch):
    monkeypatch.chdir(str(perf_env))
    for i in range(50):
        (perf_env / f"mod_{i:04d}.py").write_text(
            "import os\n\ndef foo():\n    pass\n\n" * 33
        )

    from agentnexus.tools.grep_search import grep_search
    result = benchmark(grep_search, "def ", max_results=10)
    assert "def " in result
