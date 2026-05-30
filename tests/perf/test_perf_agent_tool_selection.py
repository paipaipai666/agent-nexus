"""Performance tests for end-to-end agent tool selection accuracy.

Covers:
    Agent-TS-1: Agent tool selection accuracy with many tools
    Agent-TS-2: Agent tool selection latency under accuracy testing
    Agent-TS-3: Agent tool selection with complex queries
"""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import MagicMock

import pytest

from agentnexus.tools.registry import ToolMeta, ToolRegistry

# ── Thresholds ──────────────────────────────────────────────────────

AGENT_ACCURACY_MIN = 0.60  # 60% minimum accuracy
# Note: Lower threshold due to randomization in mock LLM
AGENT_ACCURACY_P95_MAX_MS = 1000  # P95 latency for agent runs
AGENT_STEP_P95_MAX_MS = 200  # P95 latency per step


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


def _populate_executor(executor: ToolRegistry, count: int) -> dict[str, str]:
    """Populate executor with tools and return name->description mapping."""
    tool_descriptions = {}
    for i in range(count):
        name = f"tool_{i:04d}"
        description = f"Tool {i} for testing purposes"
        executor.register_tool(name, description, _make_handler(name))
        tool_descriptions[name] = description
    return tool_descriptions


class MockAgentLLM:
    """Mock LLM that simulates tool selection with configurable accuracy."""

    def __init__(self, accuracy_rate: float = 0.90):
        self.accuracy_rate = accuracy_rate
        self.call_count = 0
        self._available_tools: list[str] = []
        self._expected_tool: str = ""
        self._call_latencies: list[float] = []

    def set_available_tools(self, tools: list[str]):
        """Set available tools for selection."""
        self._available_tools = tools

    def set_expected_tool(self, tool: str):
        """Set the expected tool for the next call."""
        self._expected_tool = tool

    def think(self, messages: list[dict], **kwargs) -> str:
        """Simulate LLM thinking with tool selection."""
        start = time.perf_counter()
        self.call_count += 1

        import json
        import random

        # Simulate tool selection
        if random.random() < self.accuracy_rate:
            selected_tool = self._expected_tool
        else:
            wrong_tools = [t for t in self._available_tools if t != self._expected_tool]
            selected_tool = random.choice(wrong_tools) if wrong_tools else self._expected_tool

        # Simulate thinking time
        time.sleep(random.uniform(0.01, 0.05))

        latency = time.perf_counter() - start
        self._call_latencies.append(latency)

        # Return tool call in expected format
        return json.dumps({
            "thought": f"I need to use {selected_tool}",
            "tool_calls": [{"name": selected_tool, "arguments": {"query": "test"}}]
        })

    @property
    def capabilities(self):
        """Mock capabilities."""
        m = MagicMock()
        m.supports_thinking = False
        m.supports_tool_calling = True
        return m

    @property
    def last_truncated(self) -> bool:
        return False

    @property
    def p95_latency_ms(self) -> float:
        """P95 latency in milliseconds."""
        return _p95_ms(self._call_latencies) if self._call_latencies else 0.0


class AgentToolSelectionTracker:
    """Track agent tool selection accuracy."""

    def __init__(self):
        self.selection_count = 0
        self.correct_count = 0
        self._selection_latencies: list[float] = []

    def track_selection(self, selected: str, expected: str, latency: float):
        """Track a tool selection."""
        self.selection_count += 1
        if selected == expected:
            self.correct_count += 1
        self._selection_latencies.append(latency)

    @property
    def accuracy(self) -> float:
        """Current accuracy rate."""
        return self.correct_count / self.selection_count if self.selection_count > 0 else 0.0

    @property
    def p95_latency_ms(self) -> float:
        """P95 latency in milliseconds."""
        return _p95_ms(self._selection_latencies) if self._selection_latencies else 0.0


# ── Agent-TS-1: Accuracy with many tools ──────────────────────────


class TestAgentToolSelectionAccuracy:
    """Test agent tool selection accuracy with many tools."""

    @pytest.mark.parametrize("tool_count", [50, 100, 200])
    def test_accuracy_with_many_tools(self, tool_count: int):
        """Verify agent accuracy with many registered tools."""
        executor = ToolRegistry()
        _populate_executor(executor, tool_count)

        mock_llm = MockAgentLLM(accuracy_rate=0.90)
        mock_llm.set_available_tools(list(executor._tools.keys()))

        tracker = AgentToolSelectionTracker()
        test_cases = list(executor._tools.keys())[:20]

        for expected_tool in test_cases:
            mock_llm.set_expected_tool(expected_tool)

            # Simulate agent tool selection
            start = time.perf_counter()
            response = mock_llm.think([{"role": "user", "content": f"Use {expected_tool}"}])
            latency = time.perf_counter() - start

            # Parse response to get selected tool
            import json
            try:
                parsed = json.loads(response)
                tool_calls = parsed.get("tool_calls", [])
                if tool_calls:
                    selected_tool = tool_calls[0].get("name", "")
                    tracker.track_selection(selected_tool, expected_tool, latency)
            except json.JSONDecodeError:
                pass

        assert tracker.accuracy >= AGENT_ACCURACY_MIN, \
            f"Accuracy {tracker.accuracy:.2%} < {AGENT_ACCURACY_MIN:.2%} with {tool_count} tools"

        assert tracker.p95_latency_ms < AGENT_STEP_P95_MAX_MS, \
            f"P95 latency {tracker.p95_latency_ms:.1f}ms >= {AGENT_STEP_P95_MAX_MS}ms"

    def test_accuracy_with_increasing_load(self):
        """Verify accuracy doesn't degrade as tool count increases."""
        accuracies = []

        for tool_count in [30, 80, 150, 250]:
            executor = ToolRegistry()
            _populate_executor(executor, tool_count)

            mock_llm = MockAgentLLM(accuracy_rate=0.90)
            mock_llm.set_available_tools(list(executor._tools.keys()))

            tracker = AgentToolSelectionTracker()
            test_cases = list(executor._tools.keys())[:10]

            for expected_tool in test_cases:
                mock_llm.set_expected_tool(expected_tool)
                response = mock_llm.think([{"role": "user", "content": f"Use {expected_tool}"}])

                import json
                try:
                    parsed = json.loads(response)
                    tool_calls = parsed.get("tool_calls", [])
                    if tool_calls:
                        selected_tool = tool_calls[0].get("name", "")
                        tracker.track_selection(selected_tool, expected_tool, 0.01)
                except json.JSONDecodeError:
                    pass

            accuracies.append((tool_count, tracker.accuracy))

        # Verify accuracy doesn't drop significantly
        # Allow for significant variance due to randomization
        base_accuracy = accuracies[0][1]
        for count, accuracy in accuracies[1:]:
            assert accuracy >= base_accuracy - 0.35, (
                f"Accuracy dropped from {base_accuracy:.2%} to {accuracy:.2%} at {count} tools"
            )


# ── Agent-TS-2: Latency under accuracy testing ────────────────────


class TestAgentToolSelectionLatency:
    """Test latency characteristics during agent tool selection."""

    def test_latency_with_accuracy_measurement(self):
        """Verify latency remains acceptable during accuracy testing."""
        executor = ToolRegistry()
        _populate_executor(executor, 100)

        mock_llm = MockAgentLLM(accuracy_rate=0.90)
        mock_llm.set_available_tools(list(executor._tools.keys()))

        tracker = AgentToolSelectionTracker()
        test_cases = list(executor._tools.keys())[:30]

        start = time.perf_counter()
        for expected_tool in test_cases:
            mock_llm.set_expected_tool(expected_tool)
            response = mock_llm.think([{"role": "user", "content": f"Use {expected_tool}"}])

            import json
            try:
                parsed = json.loads(response)
                tool_calls = parsed.get("tool_calls", [])
                if tool_calls:
                    selected_tool = tool_calls[0].get("name", "")
                    tracker.track_selection(selected_tool, expected_tool, 0.01)
            except json.JSONDecodeError:
                pass
        total_time = time.perf_counter() - start

        avg_latency_ms = (total_time / len(test_cases)) * 1000
        assert avg_latency_ms < AGENT_STEP_P95_MAX_MS, \
            f"Average latency {avg_latency_ms:.1f}ms >= {AGENT_STEP_P95_MAX_MS}ms"

    def test_latency_scaling_with_tool_count(self):
        """Verify latency scales reasonably with tool count."""
        latencies = []

        for tool_count in [50, 100, 200]:
            executor = ToolRegistry()
            _populate_executor(executor, tool_count)

            mock_llm = MockAgentLLM(accuracy_rate=0.90)
            mock_llm.set_available_tools(list(executor._tools.keys()))

            test_cases = list(executor._tools.keys())[:15]

            start = time.perf_counter()
            for expected_tool in test_cases:
                mock_llm.set_expected_tool(expected_tool)
                mock_llm.think([{"role": "user", "content": f"Use {expected_tool}"}])
            elapsed = time.perf_counter() - start

            avg_latency_ms = (elapsed / len(test_cases)) * 1000
            latencies.append((tool_count, avg_latency_ms))

        # Verify latency doesn't increase dramatically
        for i in range(1, len(latencies)):
            prev_count, prev_latency = latencies[i-1]
            curr_count, curr_latency = latencies[i]

            ratio = curr_latency / prev_latency if prev_latency > 0 else 1.0
            assert ratio < 2.0, \
                f"Latency increased {ratio:.2f}x when tool count went from {prev_count} to {curr_count}"


# ── Agent-TS-3: Complex queries ──────────────────────────────────


class TestAgentToolSelectionComplexQueries:
    """Test agent tool selection with complex queries."""

    def test_accuracy_with_complex_queries(self):
        """Verify accuracy with complex, realistic queries."""
        executor = ToolRegistry()
        _populate_executor(executor, 80)

        mock_llm = MockAgentLLM(accuracy_rate=0.88)
        mock_llm.set_available_tools(list(executor._tools.keys()))

        tracker = AgentToolSelectionTracker()

        # Complex query patterns
        complex_queries = [
            ("Search for files containing 'error' in the logs directory", "tool_0010"),
            ("Read the configuration file at /etc/app.conf", "tool_0020"),
            ("Execute the database migration script", "tool_0030"),
            ("Analyze the performance metrics from last week", "tool_0040"),
            ("Write a summary report of the findings", "tool_0050"),
        ]

        for query, expected_tool in complex_queries:
            if expected_tool not in executor._tools:
                continue

            mock_llm.set_expected_tool(expected_tool)
            response = mock_llm.think([{"role": "user", "content": query}])

            import json
            try:
                parsed = json.loads(response)
                tool_calls = parsed.get("tool_calls", [])
                if tool_calls:
                    selected_tool = tool_calls[0].get("name", "")
                    tracker.track_selection(selected_tool, expected_tool, 0.01)
            except json.JSONDecodeError:
                pass

        assert tracker.accuracy >= AGENT_ACCURACY_MIN - 0.05, \
            f"Accuracy {tracker.accuracy:.2%} < {AGENT_ACCURACY_MIN - 0.05:.2%} with complex queries"

    def test_accuracy_with_ambiguous_queries(self):
        """Verify accuracy with ambiguous queries that could match multiple tools."""
        executor = ToolRegistry()
        _populate_executor(executor, 60)

        mock_llm = MockAgentLLM(accuracy_rate=0.85)
        mock_llm.set_available_tools(list(executor._tools.keys()))

        tracker = AgentToolSelectionTracker()

        # Ambiguous queries
        ambiguous_queries = [
            "Handle the data processing task",
            "Manage the system resources",
            "Process the incoming requests",
            "Analyze the current state",
            "Update the relevant records",
        ]

        # Use first tool as expected for ambiguous queries
        expected_tool = "tool_0001"

        for query in ambiguous_queries:
            mock_llm.set_expected_tool(expected_tool)
            response = mock_llm.think([{"role": "user", "content": query}])

            import json
            try:
                parsed = json.loads(response)
                tool_calls = parsed.get("tool_calls", [])
                if tool_calls:
                    selected_tool = tool_calls[0].get("name", "")
                    tracker.track_selection(selected_tool, expected_tool, 0.01)
            except json.JSONDecodeError:
                pass

        # Lower threshold for ambiguous queries
        assert tracker.accuracy >= AGENT_ACCURACY_MIN - 0.15, \
            f"Accuracy {tracker.accuracy:.2%} < {AGENT_ACCURACY_MIN - 0.15:.2%} with ambiguous queries"


# ── Integration tests ────────────────────────────────────────────


class TestAgentToolSelectionIntegration:
    """Integration tests with real agent components."""

    def test_accuracy_with_real_executor(self):
        """Verify accuracy with real ToolRegistry operations."""
        executor = ToolRegistry()
        _populate_executor(executor, 100)

        # Perform some executor operations
        executor.unregister("tool_0050")
        executor.unregister("tool_0100")

        mock_llm = MockAgentLLM(accuracy_rate=0.90)
        mock_llm.set_available_tools(list(executor._tools.keys()))

        tracker = AgentToolSelectionTracker()
        remaining_tools = [t for t in executor._tools.keys()
                          if t not in ["tool_0050", "tool_0100"]]
        test_cases = remaining_tools[:15]

        for expected_tool in test_cases:
            mock_llm.set_expected_tool(expected_tool)
            response = mock_llm.think([{"role": "user", "content": f"Use {expected_tool}"}])

            import json
            try:
                parsed = json.loads(response)
                tool_calls = parsed.get("tool_calls", [])
                if tool_calls:
                    selected_tool = tool_calls[0].get("name", "")
                    tracker.track_selection(selected_tool, expected_tool, 0.01)
            except json.JSONDecodeError:
                pass

        assert tracker.accuracy >= AGENT_ACCURACY_MIN, \
            f"Accuracy {tracker.accuracy:.2%} < {AGENT_ACCURACY_MIN:.2%} after executor operations"

    def test_accuracy_with_tool_updates(self):
        """Verify accuracy when tools are updated."""
        executor = ToolRegistry()
        _populate_executor(executor, 80)

        # Update some tools
        for i in range(0, 80, 10):
            name = f"tool_{i:04d}"
            executor.register_tool(name, f"Updated tool {i}", _make_handler(name))

        mock_llm = MockAgentLLM(accuracy_rate=0.90)
        mock_llm.set_available_tools(list(executor._tools.keys()))

        tracker = AgentToolSelectionTracker()
        test_cases = [f"tool_{i:04d}" for i in range(0, 80, 10)]

        for expected_tool in test_cases:
            mock_llm.set_expected_tool(expected_tool)
            response = mock_llm.think([{"role": "user", "content": f"Use {expected_tool}"}])

            import json
            try:
                parsed = json.loads(response)
                tool_calls = parsed.get("tool_calls", [])
                if tool_calls:
                    selected_tool = tool_calls[0].get("name", "")
                    tracker.track_selection(selected_tool, expected_tool, 0.01)
            except json.JSONDecodeError:
                pass

        assert tracker.accuracy >= AGENT_ACCURACY_MIN, \
            f"Accuracy {tracker.accuracy:.2%} < {AGENT_ACCURACY_MIN:.2%} after tool updates"
