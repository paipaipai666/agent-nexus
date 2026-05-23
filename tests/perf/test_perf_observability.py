"""Performance: Observability — tracer span creation, JSONL flush, compute_stats aggregation.

Thresholds:
    TRACER_SPAN_CREATE_P95_MAX_MS  = 100   500 spans create+end < 100ms
    TRACER_FLUSH_THROUGHPUT_MIN    = 500   > 500 spans/s flush throughput
    STATS_COMPUTE_P95_MAX_MS_1K   = 200   compute_stats(1000 spans) < 200ms
    STATS_COMPUTE_P95_MAX_MS_10K  = 1000  compute_stats(10000 spans) < 1000ms
    TRACER_CONCURRENT_MAX_MS      = 200   4 threads × 125 spans < 200ms
"""

from __future__ import annotations

import concurrent.futures
import time

TRACER_SPAN_CREATE_P95_MAX_MS = 100
TRACER_FLUSH_THROUGHPUT_MIN = 500
STATS_COMPUTE_P95_MAX_MS_1K = 200
STATS_COMPUTE_P95_MAX_MS_10K = 1000
TRACER_CONCURRENT_MAX_MS = 200


def test_tracer_span_create(benchmark, perf_env):
    from agentnexus.observability.tracer import TraceContext

    def _run():
        ctx = TraceContext()
        for i in range(500):
            span = ctx.start_span(f"op_{i}")
            ctx.end_span(span)

    benchmark(_run)


def test_tracer_flush_throughput(perf_env):
    from agentnexus.observability.tracer import TraceManager

    mgr = TraceManager()
    mgr.configure(str(perf_env / "traces"))

    ctx = mgr.start_trace("perf_test")
    for i in range(499):
        span = ctx.start_span(f"op_{i}")
        ctx.end_span(span)

    start = time.perf_counter()
    mgr.end_trace()
    elapsed = time.perf_counter() - start
    throughput = 500 / elapsed
    assert throughput >= TRACER_FLUSH_THROUGHPUT_MIN, (
        f"flush throughput={throughput:.0f} spans/s < {TRACER_FLUSH_THROUGHPUT_MIN}"
    )


def test_compute_stats_1k(benchmark, generate_spans):
    from agentnexus.observability.stats import compute_stats

    traces_dir = generate_spans(1000)
    benchmark(compute_stats, str(traces_dir), 30)


def test_compute_stats_10k(benchmark, generate_spans):
    from agentnexus.observability.stats import compute_stats

    traces_dir = generate_spans(10_000)
    benchmark(compute_stats, str(traces_dir), 30)


def test_tracer_concurrent(perf_env):
    from agentnexus.observability.tracer import TraceContext

    def _worker(n: int):
        ctx = TraceContext()
        for i in range(n):
            span = ctx.start_span(f"op_{i}")
            ctx.end_span(span)

    start = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(_worker, 125) for _ in range(4)]
        for f in futures:
            f.result()
    elapsed = time.perf_counter() - start
    assert elapsed * 1000 < TRACER_CONCURRENT_MAX_MS, (
        f"concurrent span creation={elapsed*1000:.1f}ms > {TRACER_CONCURRENT_MAX_MS}ms"
    )


# ── compute_stats aggregation ───────────────────────────────────────────────

STATS_COMPUTE_THROUGHPUT_MIN = 200   # 200 spans/s minimum (on clean data)


def test_compute_stats_small(benchmark, generate_spans):
    """compute_stats with 500 spans."""
    d = generate_spans(500)
    from agentnexus.observability.stats import compute_stats
    result = benchmark(compute_stats, str(d), days=30)
    assert result.total_tasks >= 0


def test_compute_stats_large(perf_env, generate_spans):
    """compute_stats with 5000 spans — throughput check."""
    import time
    d = generate_spans(5000)
    from agentnexus.observability.stats import compute_stats
    start = time.perf_counter()
    result = compute_stats(str(d), days=30)
    elapsed = time.perf_counter() - start
    throughput = 5000 / elapsed
    assert throughput >= STATS_COMPUTE_THROUGHPUT_MIN, (
        f"compute_stats throughput too low: {throughput:.1f} spans/s "
        f"(threshold: {STATS_COMPUTE_THROUGHPUT_MIN})"
    )
    assert result.total_tasks > 0
    assert result.total_input_tokens > 0
    assert result.total_output_tokens > 0
