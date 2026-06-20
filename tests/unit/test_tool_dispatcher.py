"""Tests for ToolDispatcher read/write concurrent dispatch."""

from __future__ import annotations

from agentnexus.tools.dispatcher import ToolCallResult, ToolDispatcher
from agentnexus.tools.registry import ToolMeta, ToolRegistry

# ── helpers ──────────────────────────────────────────────────────


def _make_registry_with_tools() -> ToolRegistry:
    """Create a registry with a mix of concurrent-safe and unsafe tools."""
    reg = ToolRegistry()

    # Read-only tools (concurrency_safe=True)
    reg.register(
        ToolMeta(
            name="grep_search",
            description="Search code",
            param_schema={"type": "object", "properties": {}},
            concurrency_safe=True,
        ),
        lambda query="": f"grep results for {query}",
    )
    reg.register(
        ToolMeta(
            name="file_read",
            description="Read file",
            param_schema={"type": "object", "properties": {}},
            concurrency_safe=True,
        ),
        lambda path="": f"file content of {path}",
    )

    # Write tools (concurrency_safe=False, default)
    reg.register(
        ToolMeta(
            name="file_write",
            description="Write file",
            param_schema={"type": "object", "properties": {}},
        ),
        lambda path="", content="": f"wrote {path}",
    )
    reg.register(
        ToolMeta(
            name="shell_exec",
            description="Execute shell",
            param_schema={"type": "object", "properties": {}},
        ),
        lambda command="": f"executed {command}",
    )

    return reg


# ── ToolCallResult ───────────────────────────────────────────────


class TestToolCallResult:
    def test_construction(self):
        r = ToolCallResult(
            name="grep_search",
            arguments={"query": "test"},
            result="matches found",
            error=None,
            duration_ms=12.5,
        )
        assert r.name == "grep_search"
        assert r.result == "matches found"
        assert r.error is None
        assert r.duration_ms == 12.5

    def test_error_result(self):
        r = ToolCallResult(
            name="shell_exec",
            arguments={},
            result=None,
            error="timeout",
            duration_ms=60000.0,
        )
        assert r.error == "timeout"
        assert r.result is None


# ── ToolDispatcher partitioning ──────────────────────────────────


class TestToolDispatcherPartitioning:
    """Verify tools are correctly split into concurrent/sequential groups."""

    def test_single_read_only_tool(self):
        reg = _make_registry_with_tools()
        dispatcher = ToolDispatcher(reg)
        calls = [{"name": "grep_search", "arguments": {"query": "test"}, "id": "1"}]

        results = dispatcher.execute(
            calls,
            execute_fn=lambda name, args: reg.invoke(name, args),
        )
        assert len(results) == 1
        assert results[0].result == "grep results for test"
        assert results[0].error is None

    def test_single_write_tool(self):
        reg = _make_registry_with_tools()
        dispatcher = ToolDispatcher(reg)
        calls = [{"name": "file_write", "arguments": {"path": "a.txt", "content": "x"}, "id": "1"}]

        results = dispatcher.execute(
            calls,
            execute_fn=lambda name, args: reg.invoke(name, args),
        )
        assert len(results) == 1
        assert results[0].result == "wrote a.txt"

    def test_multiple_read_only_tools_concurrent(self):
        """Read-only tools should execute concurrently (all complete)."""
        reg = _make_registry_with_tools()
        dispatcher = ToolDispatcher(reg, max_workers=4)
        calls = [
            {"name": "grep_search", "arguments": {"query": "a"}, "id": "1"},
            {"name": "file_read", "arguments": {"path": "b.txt"}, "id": "2"},
            {"name": "grep_search", "arguments": {"query": "c"}, "id": "3"},
        ]

        results = dispatcher.execute(
            calls,
            execute_fn=lambda name, args: reg.invoke(name, args),
        )
        assert len(results) == 3
        assert all(r.error is None for r in results)
        # Results in original order
        assert results[0].name == "grep_search"
        assert results[1].name == "file_read"
        assert results[2].name == "grep_search"

    def test_multiple_write_tools_sequential(self):
        """Write tools should execute sequentially (all complete)."""
        reg = _make_registry_with_tools()
        dispatcher = ToolDispatcher(reg)
        calls = [
            {"name": "file_write", "arguments": {"path": "a.txt", "content": "1"}, "id": "1"},
            {"name": "shell_exec", "arguments": {"command": "ls"}, "id": "2"},
        ]

        results = dispatcher.execute(
            calls,
            execute_fn=lambda name, args: reg.invoke(name, args),
        )
        assert len(results) == 2
        assert all(r.error is None for r in results)

    def test_mixed_read_write_dispatched_correctly(self):
        """Read tools concurrent, write tools sequential, results in original order."""
        reg = _make_registry_with_tools()
        dispatcher = ToolDispatcher(reg, max_workers=4)
        calls = [
            {"name": "grep_search", "arguments": {"query": "x"}, "id": "1"},
            {"name": "file_write", "arguments": {"path": "a.txt", "content": "y"}, "id": "2"},
            {"name": "file_read", "arguments": {"path": "b.txt"}, "id": "3"},
            {"name": "shell_exec", "arguments": {"command": "echo"}, "id": "4"},
        ]

        results = dispatcher.execute(
            calls,
            execute_fn=lambda name, args: reg.invoke(name, args),
        )
        assert len(results) == 4
        # Original order preserved
        assert [r.name for r in results] == ["grep_search", "file_write", "file_read", "shell_exec"]
        assert all(r.error is None for r in results)

    def test_concurrent_tool_failure_does_not_block_others(self):
        """One failing concurrent tool should not prevent others from completing."""
        reg = _make_registry_with_tools()
        dispatcher = ToolDispatcher(reg, max_workers=4)

        call_count = {"n": 0}

        def failing_execute(name, args):
            call_count["n"] += 1
            if name == "grep_search" and args.get("query") == "fail":
                raise ValueError("search failed")
            return reg.invoke(name, args)

        calls = [
            {"name": "grep_search", "arguments": {"query": "fail"}, "id": "1"},
            {"name": "file_read", "arguments": {"path": "ok.txt"}, "id": "2"},
        ]

        results = dispatcher.execute(calls, execute_fn=failing_execute)
        assert len(results) == 2
        # First tool failed
        assert results[0].error is not None
        assert "search failed" in results[0].error
        # Second tool succeeded
        assert results[1].error is None
        assert results[1].result == "file content of ok.txt"

    def test_results_preserve_original_order(self):
        """Even with concurrent execution, results match input order."""
        reg = _make_registry_with_tools()
        dispatcher = ToolDispatcher(reg, max_workers=4)
        calls = [
            {"name": "file_read", "arguments": {"path": f"f{i}.txt"}, "id": str(i)}
            for i in range(8)
        ]

        results = dispatcher.execute(
            calls,
            execute_fn=lambda name, args: reg.invoke(name, args),
        )
        for i, r in enumerate(results):
            assert r.name == "file_read"
            assert f"f{i}.txt" in r.result


# ── concurrency_safe on ToolMeta ─────────────────────────────────


class TestConcurrencySafeField:
    def test_default_is_false(self):
        meta = ToolMeta(name="t", description="d", param_schema={})
        assert meta.concurrency_safe is False

    def test_can_set_true(self):
        meta = ToolMeta(name="t", description="d", param_schema={}, concurrency_safe=True)
        assert meta.concurrency_safe is True

    def test_register_tool_accepts_concurrency_safe(self):
        reg = ToolRegistry()
        reg.register_tool(
            name="read_only_tool",
            description="A read-only tool",
            func=lambda: "ok",
            concurrency_safe=True,
        )
        meta = reg.get_meta("read_only_tool")
        assert meta is not None
        assert meta.concurrency_safe is True

    def test_register_tool_default_concurrency_safe_false(self):
        reg = ToolRegistry()
        reg.register_tool(
            name="write_tool",
            description="A write tool",
            func=lambda: "ok",
        )
        meta = reg.get_meta("write_tool")
        assert meta is not None
        assert meta.concurrency_safe is False
