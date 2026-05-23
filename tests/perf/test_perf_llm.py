"""Performance tests for AgentNexus LLM module."""

from __future__ import annotations

import concurrent.futures
import time

import pytest

FAST_LLM_P95_MAX_MS = 100
TYPICAL_LLM_P95_MAX_MS = 1000
RETRY_OVERHEAD_P95_MAX_MS = 1500


@pytest.mark.parametrize("mock_llm_latency", ["fast"], indirect=True)
def test_llm_think_latency_fast(benchmark, mock_llm_latency):
    result = benchmark(mock_llm_latency.think, "test prompt")
    assert result is not None
    data = benchmark.stats.stats.sorted_data
    p95 = data[int(len(data) * 0.95)] * 1000 if len(data) >= 20 else data[-1] * 1000
    assert p95 < FAST_LLM_P95_MAX_MS, f"Fast LLM p95={p95:.0f}ms > {FAST_LLM_P95_MAX_MS}ms"


@pytest.mark.parametrize("mock_llm_latency", ["typical"], indirect=True)
def test_llm_think_latency_typical(benchmark, mock_llm_latency):
    result = benchmark(mock_llm_latency.think, "test prompt")
    assert result is not None
    data = benchmark.stats.stats.sorted_data
    p95 = data[int(len(data) * 0.95)] * 1000 if len(data) >= 20 else data[-1] * 1000
    assert p95 < TYPICAL_LLM_P95_MAX_MS, f"Typical LLM p95={p95:.0f}ms > {TYPICAL_LLM_P95_MAX_MS}ms"


@pytest.mark.parametrize("mock_llm_latency", ["unstable"], indirect=True)
def test_llm_think_unstable_retry(benchmark, mock_llm_latency):
    def _call():
        for _ in range(3):
            try:
                mock_llm_latency.think("prompt")
            except Exception:
                pass

    benchmark(_call)
    assert mock_llm_latency.call_count >= 3


@pytest.mark.parametrize("mock_llm_latency", ["fast"], indirect=True)
def test_llm_throughput_sequential(benchmark, mock_llm_latency):
    def _batched():
        for _ in range(5):
            mock_llm_latency.think("prompt")

    benchmark(_batched)


@pytest.mark.parametrize("mock_llm_latency", ["fast"], indirect=True)
def test_llm_concurrent_overhead(mock_llm_latency):
    start = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(mock_llm_latency.think, "prompt") for _ in range(4)]
        for f in futures:
            f.result()
    elapsed = time.perf_counter() - start
    assert elapsed < 0.3, f"Concurrent calls took {elapsed:.3f}s, expected < 0.3s"
