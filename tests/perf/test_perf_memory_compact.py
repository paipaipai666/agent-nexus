"""Performance tests for MemoryManager compaction — maybe_compact, snip, microcompact_time_based."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agentnexus.memory.manager import MemoryManager
from agentnexus.memory.short_term import ShortTermMemory


def _make_mgr(llm=None) -> MemoryManager:
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
    mgr._compact_failures = 0
    mgr._circuit_open = False
    mgr._microcompacts_since_open = 0
    mgr._compacting = False
    mgr._last_api_call_ts = 0.0
    mgr._recent_reads = []
    mgr._last_write_count = 0
    mgr._llm = llm or MagicMock()
    mgr._llm.think.return_value = "<summary>Compacted summary of conversation.</summary>"
    mgr._llm.last_truncated = False
    mgr._transcript_dir = ""
    mgr._offload_dir = ""
    mgr.session_id = "perf_test"
    return mgr


def _populate_messages(mgr: MemoryManager, count: int, role: str = "user", content_len: int = 100):
    text = "msg content " * max(content_len // 12, 1)
    for i in range(count):
        mgr.short_term.append(role, f"{text} #{i}")


def _populate_tool_results(mgr: MemoryManager, count: int):
    tools = ["read", "bash", "grep", "glob", "web_search", "web_fetch", "edit", "write"]
    for i in range(count):
        tool = tools[i % len(tools)]
        mgr.short_term.append("tool", f"Action: {tool}[key=value]\nObservation: result {i} " * 20)


# ── maybe_compact with mock LLM ──────────────────────────────


class TestMaybeCompact:
    @pytest.mark.parametrize("msg_count", [50, 200, 500])
    def test_maybe_compact_large_stm(self, benchmark, perf_env, msg_count):
        llm = MagicMock()
        llm.think.return_value = "<summary>Compacted summary of the conversation history.</summary>"
        llm.last_truncated = False

        mgr = _make_mgr(llm=llm)
        mgr._compact_threshold = 1
        _populate_messages(mgr, msg_count)

        result = benchmark(mgr.maybe_compact, threshold=1, custom_instructions="", is_auto=True)
        assert isinstance(result, int)

    def test_maybe_compact_below_threshold(self, benchmark, perf_env):
        mgr = _make_mgr()
        mgr._compact_threshold = 120000
        _populate_messages(mgr, 10)

        result = benchmark(mgr.maybe_compact)
        assert result == 0

    def test_maybe_compact_circuit_breaker(self, benchmark, perf_env):
        llm = MagicMock()
        llm.think.return_value = ""
        llm.last_truncated = False

        mgr = _make_mgr(llm=llm)
        mgr._compact_threshold = 1
        mgr._circuit_open = True
        _populate_messages(mgr, 100)

        result = benchmark(mgr.maybe_compact, threshold=1)
        assert result == 0


# ── snip ──────────────────────────────────────────────────────


class TestSnip:
    @pytest.mark.parametrize("count", [100, 500])
    def test_snip_scaling(self, benchmark, count):
        mgr = _make_mgr()
        _populate_messages(mgr, count)

        result = benchmark(mgr.snip)
        assert isinstance(result, int)

    def test_snip_small_stm(self, benchmark):
        mgr = _make_mgr()
        _populate_messages(mgr, 5)

        result = benchmark(mgr.snip)
        assert result == 0


# ── microcompact_time_based ───────────────────────────────────


class TestMicrocompactTimeBased:
    def test_microcompact_time_based_triggers(self, benchmark, perf_env):
        import time as _time

        mgr = _make_mgr()
        _populate_tool_results(mgr, 50)
        mgr._last_api_call_ts = _time.time() - 9999
        mgr._settings.time_microcompact_interval = 1

        result = benchmark(mgr.microcompact_time_based)
        assert isinstance(result, bool)

    def test_microcompact_time_based_no_trigger(self, benchmark, perf_env):
        import time as _time

        mgr = _make_mgr()
        _populate_tool_results(mgr, 50)
        mgr._last_api_call_ts = _time.time()
        mgr._settings.time_microcompact_interval = 9999

        result = benchmark(mgr.microcompact_time_based)
        assert result is False

    def test_microcompact_time_based_no_api_call(self, benchmark, perf_env):
        mgr = _make_mgr()
        _populate_tool_results(mgr, 50)
        mgr._last_api_call_ts = 0.0

        result = benchmark(mgr.microcompact_time_based)
        assert result is False
