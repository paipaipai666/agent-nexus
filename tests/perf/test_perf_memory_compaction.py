"""Performance: MemoryManager compaction benchmarks (snip, microcompact, projection)."""
from __future__ import annotations

from unittest.mock import MagicMock

from agentnexus.memory.manager import MemoryManager
from agentnexus.memory.short_term import ShortTermMemory

_RECOVERABLE_TOOLS = frozenset({
    "read", "bash", "grep", "glob", "web_search", "web_fetch",
    "edit", "write", "search",
})


def _make_mgr():
    mgr = MemoryManager.__new__(MemoryManager)
    mgr.short_term = ShortTermMemory()
    mgr._settings = MagicMock()
    mgr._settings.snip_enabled = True
    mgr._settings.time_microcompact_interval = 0
    mgr._settings.large_result_threshold = 100000
    mgr._snip_freed_tokens = 0
    mgr._on_compact = None
    mgr._on_after_compact = None
    mgr._ctx_max = 128000
    mgr._compact_threshold = 120000
    return mgr


def _populate_messages(mgr, count: int, role: str = "user", content_len: int = 100):
    text = "msg content " * (max(content_len // 12, 1))
    for i in range(count):
        mgr.short_term.append(role, f"{text} #{i}")


def _populate_tool_results(mgr, count: int):
    tools = list(_RECOVERABLE_TOOLS)
    for i in range(count):
        tool = tools[i % len(tools)]
        mgr.short_term.append("tool", f"Action: {tool}[key=value]\nObservation: result {i} " * 20)


class TestSnipBenchmark:
    def test_snip_1000_messages(self, benchmark):
        mgr = _make_mgr()
        _populate_messages(mgr, 1000)
        result = benchmark(mgr.snip)
        assert isinstance(result, int)


class TestMicroCompactBenchmark:
    def test_microcompact_200_tool_results(self, benchmark):
        mgr = _make_mgr()
        _populate_tool_results(mgr, 200)
        result = benchmark(mgr.microcompact)
        assert result is None


class TestBuildProjectionBenchmark:
    def test_build_projection_500_messages_90pct(self, benchmark):
        mgr = _make_mgr()
        mgr._ctx_max = 500
        _populate_messages(mgr, 500, "assistant", 200)
        messages = mgr.short_term.get_all()
        result = benchmark(mgr.build_projection, messages)
        assert isinstance(result, list)


class TestProjectAggressiveBenchmark:
    def test_project_aggressive_500_messages(self, benchmark):
        mgr = _make_mgr()
        messages = [
            {"role": "assistant" if i % 2 == 0 else "tool",
             "content": "content " * 200}
            for i in range(500)
        ]
        result = benchmark(mgr._project_aggressive, messages)
        assert isinstance(result, list)


class TestEstimateTokensBenchmark:
    def test_estimate_tokens_1000_messages(self, benchmark):
        mgr = _make_mgr()
        _populate_messages(mgr, 1000, "assistant", 200)
        mgr.short_term.estimate_tokens()
        result = benchmark(mgr.short_term.estimate_tokens)
        assert isinstance(result, int)


class TestProjectMildBenchmark:
    def test_project_mild_500_messages(self, benchmark):
        mgr = _make_mgr()
        messages = [
            {"role": "assistant" if i % 2 == 0 else "tool",
             "content": "content " * 200}
            for i in range(500)
        ]
        result = benchmark(mgr._project_mild, messages)
        assert isinstance(result, list)
