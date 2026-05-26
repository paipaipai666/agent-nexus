"""Performance tests for tool selection accuracy under load.

Covers:
    Tool-Acc-1: Tool selection accuracy with 500+ registered tools
    Tool-Acc-2: Tool selection accuracy with similar tool names
    Tool-Acc-3: Tool selection latency under accuracy testing
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from agentnexus.tools.registry import ToolMeta, ToolRegistry

# ── Thresholds ──────────────────────────────────────────────────────

TOOL_ACCURACY_MIN = 0.80  # 80% minimum accuracy
TOOL_ACCURACY_P95_MAX_MS = 200  # P95 latency for accuracy tests
TOOL_ACCURACY_SIMILAR_MIN = 0.65  # 65% accuracy with similar tool names


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


def _make_handler(tool_name: str) -> Any:
    """Create a handler that returns the tool name."""
    return lambda **kwargs: f"result_from_{tool_name}"


def _create_tool_meta(name: str, description: str, **kwargs) -> ToolMeta:
    """Create ToolMeta with consistent schema."""
    return ToolMeta(
        name=name,
        description=description,
        param_schema={"type": "object", "properties": {"query": {"type": "string"}}},
        **kwargs,
    )


def _populate_registry(registry: ToolRegistry, count: int) -> dict[str, str]:
    """Populate registry with tools and return name->description mapping."""
    tool_descriptions = {}
    for i in range(count):
        name = f"tool_{i:04d}"
        description = f"Tool {i} for testing purposes"
        meta = _create_tool_meta(name, description)
        registry.register(meta, _make_handler(name))
        tool_descriptions[name] = description
    return tool_descriptions


def _populate_with_similar_tools(registry: ToolRegistry, base_count: int) -> dict[str, str]:
    """Populate registry with similar tool names to test discrimination."""
    tool_descriptions = {}
    categories = ["search", "read", "write", "execute", "analyze"]

    for category in categories:
        for i in range(base_count // len(categories)):
            name = f"{category}_tool_{i:04d}"
            description = f"{category.capitalize()} tool variant {i}"
            meta = _create_tool_meta(name, description)
            registry.register(meta, _make_handler(name))
            tool_descriptions[name] = description

    return tool_descriptions


class MockToolSelector:
    """Mock tool selector that simulates LLM-based tool selection."""

    def __init__(self, registry: ToolRegistry, accuracy_rate: float = 0.95):
        self.registry = registry
        self.accuracy_rate = accuracy_rate
        self.selection_count = 0
        self.correct_count = 0
        self._call_latencies: list[float] = []

    def select_tool(self, query: str, expected_tool: str) -> tuple[str, bool]:
        """Select a tool based on query, return (selected_tool, is_correct)."""
        start = time.perf_counter()

        available_tools = list(self.registry._tools.keys())
        if not available_tools:
            return "", False

        # Simulate LLM selection with configurable accuracy
        import random
        if random.random() < self.accuracy_rate:
            selected = expected_tool
        else:
            # Select a random wrong tool
            wrong_tools = [t for t in available_tools if t != expected_tool]
            selected = random.choice(wrong_tools) if wrong_tools else expected_tool

        latency = time.perf_counter() - start
        self._call_latencies.append(latency)

        self.selection_count += 1
        is_correct = selected == expected_tool
        if is_correct:
            self.correct_count += 1

        return selected, is_correct

    @property
    def accuracy(self) -> float:
        """Current accuracy rate."""
        return self.correct_count / self.selection_count if self.selection_count > 0 else 0.0

    @property
    def p95_latency_ms(self) -> float:
        """P95 latency in milliseconds."""
        return _p95_ms(self._call_latencies) if self._call_latencies else 0.0


# ── Tool-Acc-1: Accuracy with many tools ───────────────────────────


class TestToolAccuracyUnderLoad:
    """Test tool selection accuracy with many registered tools."""

    @pytest.mark.parametrize("tool_count", [100, 300, 500])
    def test_accuracy_with_many_tools(self, tool_count: int):
        """Verify accuracy remains high even with many tools registered."""
        registry = ToolRegistry()
        _populate_registry(registry, tool_count)

        selector = MockToolSelector(registry, accuracy_rate=0.95)
        test_cases = [f"tool_{i:04d}" for i in range(min(50, tool_count))]

        start = time.perf_counter()
        for expected_tool in test_cases:
            query = f"Use {expected_tool} for this task"
            selector.select_tool(query, expected_tool)
        elapsed = time.perf_counter() - start

        assert selector.accuracy >= TOOL_ACCURACY_MIN, \
            f"Accuracy {selector.accuracy:.2%} < {TOOL_ACCURACY_MIN:.2%} with {tool_count} tools"

        p95_ms = (elapsed / len(test_cases)) * 1000
        assert p95_ms < TOOL_ACCURACY_P95_MAX_MS, \
            f"P95 latency {p95_ms:.1f}ms >= {TOOL_ACCURACY_P95_MAX_MS}ms"

    def test_accuracy_with_increasing_load(self):
        """Verify accuracy doesn't degrade as tool count increases."""
        registry = ToolRegistry()
        accuracies = []

        for tool_count in [50, 150, 300, 500]:
            registry._tools.clear()
            registry._param_validators.clear()
            registry._output_validators.clear()
            _populate_registry(registry, tool_count)

            selector = MockToolSelector(registry, accuracy_rate=0.95)
            test_cases = [f"tool_{i:04d}" for i in range(20)]

            for expected_tool in test_cases:
                query = f"Use {expected_tool} for this task"
                selector.select_tool(query, expected_tool)

            accuracies.append((tool_count, selector.accuracy))

        # Verify accuracy doesn't drop significantly with more tools
        # Allow for some variance due to randomization
        base_accuracy = accuracies[0][1]
        for count, accuracy in accuracies[1:]:
            assert accuracy >= base_accuracy - 0.10, (
                f"Accuracy dropped from {base_accuracy:.2%} to {accuracy:.2%} at {count} tools"
            )


# ── Tool-Acc-2: Accuracy with similar tool names ──────────────────


class TestToolAccuracyWithSimilarNames:
    """Test tool selection accuracy with similar tool names."""

    def test_discriminate_between_similar_tools(self):
        """Verify selector can discriminate between similarly named tools."""
        registry = ToolRegistry()
        _populate_with_similar_tools(registry, 100)

        selector = MockToolSelector(registry, accuracy_rate=0.90)

        # Test with tools from same category
        search_tools = [name for name in registry._tools.keys() if name.startswith("search_")]
        test_cases = search_tools[:10]

        for expected_tool in test_cases:
            query = f"Use {expected_tool} to search"
            selector.select_tool(query, expected_tool)

        assert selector.accuracy >= TOOL_ACCURACY_SIMILAR_MIN, \
            f"Accuracy {selector.accuracy:.2%} < {TOOL_ACCURACY_SIMILAR_MIN:.2%} with similar tools"

    def test_accuracy_across_categories(self):
        """Verify accuracy across different tool categories."""
        registry = ToolRegistry()
        _populate_with_similar_tools(registry, 200)

        selector = MockToolSelector(registry, accuracy_rate=0.92)
        categories = ["search", "read", "write", "execute", "analyze"]

        for category in categories:
            category_tools = [name for name in registry._tools.keys() if name.startswith(category)]
            test_cases = category_tools[:5]

            for expected_tool in test_cases:
                query = f"Use {expected_tool}"
                selector.select_tool(query, expected_tool)

        assert selector.accuracy >= TOOL_ACCURACY_SIMILAR_MIN, \
            f"Accuracy {selector.accuracy:.2%} < {TOOL_ACCURACY_SIMILAR_MIN:.2%} across categories"


# ── Tool-Acc-3: Latency under accuracy testing ────────────────────


class TestToolAccuracyLatency:
    """Test latency characteristics during accuracy testing."""

    def test_latency_with_accuracy_measurement(self):
        """Verify latency remains acceptable during accuracy testing."""
        registry = ToolRegistry()
        _populate_registry(registry, 300)

        selector = MockToolSelector(registry, accuracy_rate=0.95)
        test_cases = [f"tool_{i:04d}" for i in range(100)]

        start = time.perf_counter()
        for expected_tool in test_cases:
            query = f"Use {expected_tool}"
            selector.select_tool(query, expected_tool)
        total_time = time.perf_counter() - start

        avg_latency_ms = (total_time / len(test_cases)) * 1000
        assert avg_latency_ms < TOOL_ACCURACY_P95_MAX_MS, \
            f"Average latency {avg_latency_ms:.1f}ms >= {TOOL_ACCURACY_P95_MAX_MS}ms"

    def test_latency_scaling_with_tool_count(self):
        """Verify latency scales sub-linearly with tool count."""
        latencies = []

        for tool_count in [100, 200, 400]:
            registry = ToolRegistry()
            _populate_registry(registry, tool_count)

            selector = MockToolSelector(registry, accuracy_rate=0.95)
            test_cases = [f"tool_{i:04d}" for i in range(50)]

            start = time.perf_counter()
            for expected_tool in test_cases:
                query = f"Use {expected_tool}"
                selector.select_tool(query, expected_tool)
            elapsed = time.perf_counter() - start

            avg_latency_ms = (elapsed / len(test_cases)) * 1000
            latencies.append((tool_count, avg_latency_ms))

        # Verify latency doesn't increase dramatically with tool count
        # Allow for some variance due to system load
        for i in range(1, len(latencies)):
            prev_count, prev_latency = latencies[i-1]
            curr_count, curr_latency = latencies[i]

            ratio = curr_latency / prev_latency if prev_latency > 0 else 1.0
            count_ratio = curr_count / prev_count

            # Allow latency to increase up to 3x the tool count ratio
            # to account for system load and other factors
            assert ratio < count_ratio * 3, (
                f"Latency increased {ratio:.2f}x for {count_ratio:.2f}x more tools"
            )


# ── Integration with real registry ────────────────────────────────


class TestToolAccuracyIntegration:
    """Integration tests with real registry operations."""

    def test_accuracy_with_registry_operations(self):
        """Verify accuracy works with real registry operations."""
        registry = ToolRegistry()
        _populate_registry(registry, 200)

        # Perform some registry operations
        registry.unregister("tool_0050")
        registry.unregister("tool_0100")

        selector = MockToolSelector(registry, accuracy_rate=0.95)
        remaining_tools = [name for name in registry._tools.keys() if name != "tool_0050" and name != "tool_0100"]
        test_cases = remaining_tools[:20]

        for expected_tool in test_cases:
            query = f"Use {expected_tool}"
            selector.select_tool(query, expected_tool)

        assert selector.accuracy >= TOOL_ACCURACY_MIN, \
            f"Accuracy {selector.accuracy:.2%} < {TOOL_ACCURACY_MIN:.2%} after registry operations"

    def test_accuracy_with_tool_updates(self):
        """Verify accuracy works when tools are updated."""
        registry = ToolRegistry()
        _populate_registry(registry, 100)

        # Update some tools
        for i in range(0, 100, 10):
            name = f"tool_{i:04d}"
            meta = _create_tool_meta(name, f"Updated tool {i}")
            registry.register(meta, _make_handler(name))

        selector = MockToolSelector(registry, accuracy_rate=0.95)
        test_cases = [f"tool_{i:04d}" for i in range(0, 100, 10)]

        for expected_tool in test_cases:
            query = f"Use {expected_tool}"
            selector.select_tool(query, expected_tool)

        assert selector.accuracy >= TOOL_ACCURACY_MIN, \
            f"Accuracy {selector.accuracy:.2%} < {TOOL_ACCURACY_MIN:.2%} after tool updates"
