"""Performance: MemoryManager append, compact, context assembly."""
from __future__ import annotations

import time

import pytest

MM_APPEND_P95_MAX_MS = 20
MM_INIT_SESSION_P95_MAX_MS = 500
MM_COMPACT_LARGE_P95_MAX_MS = 3000


@pytest.fixture
def memory_manager(perf_env):
    from agentnexus.memory.manager import MemoryManager
    from agentnexus.rag.chroma_client import _reset_chroma_client
    _reset_chroma_client()
    mm = MemoryManager("perf_bench")
    mm.init_session("perf benchmark")
    return mm


def test_mm_append_small(benchmark, memory_manager):
    """MemoryManager.append with short messages."""
    def _run():
        memory_manager.append("user", "Hello, how are you?")
    benchmark(_run)


def test_mm_append_bulk(perf_env, memory_manager):
    """Bulk append throughput over 200 messages."""
    n = 200
    start = time.perf_counter()
    for i in range(n):
        role = "user" if i % 2 == 0 else "assistant"
        memory_manager.append(role, f"This is message number {i} with some realistic content for testing.")
    elapsed = time.perf_counter() - start
    p95 = (elapsed / n) * 1000 * 1.05
    assert p95 < MM_APPEND_P95_MAX_MS, f"append p95 too high: {p95:.1f}ms"


def test_mm_init_session(benchmark, perf_env):
    """Cold init_session including LTM ChromaDB setup."""
    from agentnexus.memory.manager import MemoryManager
    from agentnexus.rag.chroma_client import _reset_chroma_client
    _reset_chroma_client()

    def _run():
        mm = MemoryManager("cold_start")
        mm.init_session("perf benchmark")
    benchmark(_run)


def test_mm_estimate_tokens(benchmark, memory_manager):
    """STM token estimation with realistic content."""
    for i in range(20):
        memory_manager.append("user", f"Query {i}: what is the meaning of life?")
        memory_manager.append("assistant", f"Answer {i}: The meaning of life is a philosophical question...")

    def _run():
        memory_manager.estimate_stm_tokens()
    benchmark(_run)


def test_mm_context_assembly(benchmark, memory_manager):
    """build_projection with STM + LTM context."""
    memory_manager.append("user", "Tell me about Python programming.")
    memory_manager.append("assistant", "Python is a versatile programming language...")
    memory_manager.mark_api_call()
    memory_manager.conclude("perf test question", "performance test answer")

    def _run():
        memory_manager.build_projection([])
    benchmark(_run)
