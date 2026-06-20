"""Tool dispatcher — read/write partitioned execution.

When the LLM returns multiple tool_calls in one turn, the dispatcher
partitions them into concurrent-safe (read-only) and sequential (write)
groups.  Read-only tools run in parallel via a thread pool; write tools
run one by one to avoid conflicts.  Results are returned in the original
tool_calls order.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Callable

from agentnexus.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


@dataclass
class ToolCallResult:
    """Result of a single tool call within a batch dispatch."""

    name: str
    arguments: dict
    result: Any = None
    error: str | None = None
    duration_ms: float = 0.0


@dataclass
class _IndexedCall:
    """Internal wrapper that preserves the original index."""

    index: int
    name: str
    arguments: dict
    call_id: str = ""


class ToolDispatcher:
    """Partitions tool calls into concurrent/sequential groups and executes them.

    Args:
        registry: ToolRegistry to query ``concurrency_safe`` metadata.
        max_workers: Thread pool size for concurrent tools (default 4).
    """

    def __init__(self, registry: ToolRegistry, max_workers: int = 4) -> None:
        self._registry = registry
        self._max_workers = max_workers

    def execute(
        self,
        tool_calls: list[dict],
        execute_fn: Callable[[str, dict], Any],
    ) -> list[ToolCallResult]:
        """Execute a batch of tool calls with read/write partitioning.

        Args:
            tool_calls: List of dicts with ``name``, ``arguments``, and
                optional ``id`` keys.
            execute_fn: ``(name, arguments) -> result`` — the actual tool
                execution function (typically ``registry.invoke``).

        Returns:
            Results in the same order as *tool_calls*.
        """
        if not tool_calls:
            return []

        # Partition into concurrent-safe and sequential groups
        concurrent_group: list[_IndexedCall] = []
        sequential_group: list[_IndexedCall] = []

        for i, tc in enumerate(tool_calls):
            indexed = _IndexedCall(
                index=i,
                name=tc["name"],
                arguments=tc.get("arguments", {}),
                call_id=tc.get("id", ""),
            )
            meta = self._registry.get_meta(tc["name"])
            if meta and meta.concurrency_safe:
                concurrent_group.append(indexed)
            else:
                sequential_group.append(indexed)

        # Pre-allocate results array to preserve order
        results: list[ToolCallResult | None] = [None] * len(tool_calls)

        # Execute concurrent group in parallel
        if concurrent_group:
            self._execute_concurrent(concurrent_group, execute_fn, results)

        # Execute sequential group one by one
        if sequential_group:
            self._execute_sequential(sequential_group, execute_fn, results)

        # All slots should be filled
        return [r for r in results if r is not None]

    def _execute_concurrent(
        self,
        calls: list[_IndexedCall],
        execute_fn: Callable[[str, dict], Any],
        results: list[ToolCallResult | None],
    ) -> None:
        """Run concurrent-safe tools in a thread pool."""
        with ThreadPoolExecutor(max_workers=self._max_workers) as pool:
            future_to_call = {
                pool.submit(self._run_single, call, execute_fn): call
                for call in calls
            }
            for future in as_completed(future_to_call):
                call = future_to_call[future]
                try:
                    results[call.index] = future.result()
                except Exception as exc:
                    # Should not happen — _run_single catches everything
                    results[call.index] = ToolCallResult(
                        name=call.name,
                        arguments=call.arguments,
                        error=str(exc),
                    )

    def _execute_sequential(
        self,
        calls: list[_IndexedCall],
        execute_fn: Callable[[str, dict], Any],
        results: list[ToolCallResult | None],
    ) -> None:
        """Run write tools one by one."""
        for call in calls:
            results[call.index] = self._run_single(call, execute_fn)

    @staticmethod
    def _run_single(
        call: _IndexedCall,
        execute_fn: Callable[[str, dict], Any],
    ) -> ToolCallResult:
        """Execute a single tool call and capture timing/errors."""
        start = time.monotonic()
        try:
            result = execute_fn(call.name, call.arguments)
            duration = (time.monotonic() - start) * 1000
            return ToolCallResult(
                name=call.name,
                arguments=call.arguments,
                result=result,
                duration_ms=round(duration, 1),
            )
        except Exception as exc:
            duration = (time.monotonic() - start) * 1000
            return ToolCallResult(
                name=call.name,
                arguments=call.arguments,
                error=str(exc),
                duration_ms=round(duration, 1),
            )
