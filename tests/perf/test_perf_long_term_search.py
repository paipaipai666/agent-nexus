"""Performance: Long-term memory search, save, eviction benchmarks.

Requires perf_env fixture for AGENTNEXUS_HOME isolation.
"""
from __future__ import annotations


def _reset_ltm():
    from agentnexus.memory.long_term import _reset_long_term_memory
    _reset_long_term_memory()


def _get_ltm():
    from agentnexus.memory.long_term import get_long_term_memory
    ltm = get_long_term_memory()
    ltm.clear_all()
    return ltm


def _save_entries(ltm, count: int, session: str = "perf_bench"):
    for i in range(count):
        ltm.save(
            session_id=session,
            content=f"benchmark memory entry #{i} with some sample content for long term storage testing purposes",
            category="entity_fact",
            importance=0.5,
        )


class TestLtmSaveBenchmark:
    def test_save_single_entry(self, benchmark, perf_env):
        _reset_ltm()
        ltm = _get_ltm()

        def _run():
            ltm.save(
                session_id="perf_bench",
                content="single entry for save benchmark",
                category="entity_fact",
                importance=0.5,
            )
        benchmark(_run)

    def test_save_bulk_100_entries(self, benchmark, perf_env):
        _reset_ltm()
        ltm = _get_ltm()

        def _run():
            _save_entries(ltm, 100, "bulk_bench")
        benchmark(_run)


class TestLtmSearchBenchmark:
    def test_search_with_100_entries(self, benchmark, perf_env):
        _reset_ltm()
        ltm = _get_ltm()
        _save_entries(ltm, 100)

        def _run():
            ltm.search(query_embedding=None, limit=10)
        benchmark(_run)

    def test_search_with_1000_entries(self, benchmark, perf_env):
        _reset_ltm()
        ltm = _get_ltm()
        _save_entries(ltm, 1000)

        def _run():
            ltm.search(query_embedding=None, limit=10)
        benchmark(_run)


class TestLtmEvictBenchmark:
    def test_evict_if_needed_at_capacity(self, benchmark, perf_env):
        _reset_ltm()
        ltm = _get_ltm()
        ltm._max_memories = 150
        _save_entries(ltm, 200)
        # Reset write counter to avoid side effects in benchmark
        ltm._write_counter = 0

        # Add one more to trigger eviction directly
        def _run():
            ltm.save(
                session_id="evict_bench",
                content="trigger eviction entry",
                category="entity_fact",
                importance=0.1,
            )
        benchmark(_run)


class TestLtmListRecentBenchmark:
    def test_list_recent_with_1000_entries(self, benchmark, perf_env):
        _reset_ltm()
        ltm = _get_ltm()
        _save_entries(ltm, 1000)

        def _run():
            ltm.list_recent(limit=50)
        benchmark(_run)
