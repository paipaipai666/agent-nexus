"""Performance: Conversation version control — commit, undo, log, concurrent writes.

Thresholds:
    VERSIONED_COMMIT_THROUGHPUT_MIN = 200       50 commits > 200 commits/s
    VERSIONED_UNDO_P95_MAX_MS       = 20        per undo in 50-step chain < 20ms
    VERSIONED_LOG_P95_MAX_MS_100    = 100       100-depth log(all_branches=False) < 100ms
    VERSIONED_CONCURRENT_MAX_S      = 2.0       4 threads × 25 commits < 2s
"""

from __future__ import annotations

import concurrent.futures
import time

VERSIONED_COMMIT_THROUGHPUT_MIN = 200
VERSIONED_UNDO_P95_MAX_MS = 20
VERSIONED_LOG_P95_MAX_MS_100 = 100
VERSIONED_CONCURRENT_MAX_S = 2.0


def test_versioned_commit_throughput(perf_env):
    from agentnexus.memory.versioned import ConversationVersionManager

    mgr = ConversationVersionManager("perf", str(perf_env / "ver.db"))
    n = 50
    start = time.perf_counter()
    for i in range(n):
        mgr.commit("{}", question=f"q_{i}")
    elapsed = time.perf_counter() - start
    throughput = n / elapsed
    assert throughput >= VERSIONED_COMMIT_THROUGHPUT_MIN, (
        f"commit throughput={throughput:.0f} commits/s < {VERSIONED_COMMIT_THROUGHPUT_MIN}"
    )


def test_versioned_undo_chain(perf_env):
    from agentnexus.memory.versioned import ConversationVersionManager

    mgr = ConversationVersionManager("perf", str(perf_env / "ver2.db"))
    for i in range(50):
        mgr.commit("{}", question=f"q_{i}")

    times = []
    for _ in range(50):
        start = time.perf_counter()
        mgr.undo()
        times.append(time.perf_counter() - start)

    times.sort()
    p95 = times[int(len(times) * 0.95)] * 1000
    assert p95 < VERSIONED_UNDO_P95_MAX_MS, (
        f"undo p95={p95:.1f}ms > {VERSIONED_UNDO_P95_MAX_MS}ms"
    )


def test_versioned_log_deep(perf_env):
    from agentnexus.memory.versioned import ConversationVersionManager

    mgr = ConversationVersionManager("perf", str(perf_env / "ver3.db"))
    for i in range(100):
        mgr.commit("{}", question=f"q_{i}")

    times = []
    for _ in range(10):
        start = time.perf_counter()
        mgr.log(all_branches=False)
        times.append(time.perf_counter() - start)

    times.sort()
    p95 = times[int(len(times) * 0.95)] * 1000
    assert p95 < VERSIONED_LOG_P95_MAX_MS_100, (
        f"log p95={p95:.1f}ms > {VERSIONED_LOG_P95_MAX_MS_100}ms"
    )


def test_versioned_concurrent_write(perf_env):
    from agentnexus.memory.versioned import ConversationVersionManager
    db_path = str(perf_env / "ver4.db")

    def _commit(session_id: str, n: int):
        mgr = ConversationVersionManager(session_id, db_path)
        for i in range(n):
            mgr.commit("{}", question=f"conc_{session_id}_{i}")

    start = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(_commit, f"perf_conc_{j}", 25) for j in range(4)]
        for f in futures:
            f.result()
    elapsed = time.perf_counter() - start
    assert elapsed < VERSIONED_CONCURRENT_MAX_S, (
        f"concurrent write={elapsed:.2f}s > {VERSIONED_CONCURRENT_MAX_S}s"
    )
