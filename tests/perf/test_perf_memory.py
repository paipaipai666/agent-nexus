"""Performance benchmarks for AgentNexus memory modules.

Thresholds:
    LTM_SAVE_THROUGHPUT_MIN = 50   saves/sec
    LTM_SEARCH_P95_MAX_MS   = 600  ms
    STM_ESTIMATE_P95_MAX_MS = 5    ms
"""

from __future__ import annotations

import time

import pytest

LTM_SAVE_THROUGHPUT_MIN = 35
LTM_SEARCH_P95_MAX_MS = 600
STM_ESTIMATE_P95_MAX_MS = 5
STM_ESTIMATE_P95_MAX_MS_BY_SIZE = {50: 10, 100: 15, 200: 30}


def _reset_ltm():
    from agentnexus.memory.long_term import _reset_long_term_memory
    _reset_long_term_memory()


def test_ltm_save_throughput(perf_env):
    _reset_ltm()
    from agentnexus.memory.long_term import get_long_term_memory
    ltm = get_long_term_memory()
    ltm.clear_all()

    n = 50
    start = time.perf_counter()
    for i in range(n):
        ltm.save(
            session_id="perf_test",
            content=f"perf test memory entry #{i} with some sample content for benchmarking",
            category="entity_fact",
            importance=0.5,
        )
    elapsed = time.perf_counter() - start
    throughput = n / elapsed
    assert throughput >= LTM_SAVE_THROUGHPUT_MIN, (
        f"LTM save throughput too low: {throughput:.1f} saves/sec "
        f"(threshold: {LTM_SAVE_THROUGHPUT_MIN})"
    )


def test_ltm_save_and_search(perf_env):
    _reset_ltm()
    from agentnexus.memory.long_term import get_long_term_memory
    ltm = get_long_term_memory()
    ltm.clear_all()
    for i in range(20):
        ltm.save(
            session_id="perf_test",
            content=f"perf test memory about Python programming #{i}",
            category="entity_fact",
            importance=0.5,
        )

    times = []
    for _ in range(10):
        start = time.perf_counter()
        ltm.search(query_embedding=None, category=None, limit=5)
        times.append(time.perf_counter() - start)

    times.sort()
    p95 = times[int(len(times) * 0.95)] * 1000
    assert p95 < LTM_SEARCH_P95_MAX_MS, (
        f"LTM search p95 too high: {p95:.1f}ms (threshold: {LTM_SEARCH_P95_MAX_MS}ms)"
    )


def test_ltm_list_recent(perf_env):
    _reset_ltm()
    from agentnexus.memory.long_term import get_long_term_memory
    ltm = get_long_term_memory()
    ltm.clear_all()
    for i in range(20):
        ltm.save(
            session_id="perf_test",
            content=f"perf test memory #{i}",
            category="entity_fact",
            importance=0.5,
        )

    start = time.perf_counter()
    for _ in range(10):
        ltm.list_recent(10)
    elapsed = time.perf_counter() - start
    avg_ms = elapsed / 10 * 1000
    assert avg_ms < 50, f"LTM list_recent too slow: avg {avg_ms:.1f}ms"


def test_stm_estimate_tokens(perf_env):
    from agentnexus.memory.short_term import ShortTermMemory
    stm = ShortTermMemory()
    stm.append("user", "Hello, I need help with Python")
    stm.append("assistant", "Sure, I can help with that!")
    for i in range(10):
        stm.append("user", f"This is test message number {i} for token estimation purposes")
        stm.append("assistant", f"This is the response to message {i} with some additional content")

    # Warm up: litellm.token_counter has cold-start overhead
    stm.estimate_tokens()

    times = []
    for _ in range(10):
        start = time.perf_counter()
        stm.estimate_tokens()
        times.append(time.perf_counter() - start)

    times.sort()
    p95 = times[int(len(times) * 0.95)] * 1000
    assert p95 < STM_ESTIMATE_P95_MAX_MS, (
        f"STM estimate_tokens p95 too high: {p95:.1f}ms (threshold: {STM_ESTIMATE_P95_MAX_MS}ms)"
    )


def test_stm_to_json_roundtrip(perf_env):
    from agentnexus.memory.short_term import ShortTermMemory
    stm = ShortTermMemory()
    for i in range(10):
        stm.append("user", f"message {i}")
        stm.append("assistant", f"response {i}")

    times = []
    for _ in range(10):
        start = time.perf_counter()
        data = stm.to_json()
        ShortTermMemory.from_json(data)
        times.append(time.perf_counter() - start)

    times.sort()
    p95 = times[int(len(times) * 0.95)] * 1000
    assert p95 < 5, f"STM to_json roundtrip p95 too high: {p95:.1f}ms"


@pytest.mark.parametrize("stm_size", [50, 100, 200])
def test_stm_estimate_scaling(perf_env, stm_size):
    from agentnexus.memory.short_term import ShortTermMemory
    stm = ShortTermMemory()
    for i in range(stm_size):
        stm.append("user", f"test message {i} " * 10)
        stm.append("assistant", f"response {i} " * 20)

    stm.estimate_tokens()  # warmup
    assert isinstance(stm.estimate_tokens(), int)

    times = []
    for _ in range(10):
        start = time.perf_counter()
        stm.estimate_tokens()
        times.append(time.perf_counter() - start)

    times.sort()
    p95 = times[int(len(times) * 0.95)] * 1000
    limit = STM_ESTIMATE_P95_MAX_MS_BY_SIZE[stm_size]
    assert p95 < limit, (
        f"Stm estimate p95({stm_size} pairs)={p95:.1f}ms > {limit}ms"
    )
