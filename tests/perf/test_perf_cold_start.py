"""Performance: Cold start — first import of agentnexus.

Thresholds:
    COLD_START_IMPORT_MAX_S = 5.0   average of 3 cold imports < 5s
"""

from __future__ import annotations

import subprocess
import sys
import time

COLD_START_IMPORT_MAX_S = 5.0


def test_cold_start_import():
    times = []
    for _ in range(3):
        start = time.perf_counter()
        subprocess.check_output(
            [sys.executable, "-c", "import agentnexus"],
            timeout=30,
            stderr=subprocess.STDOUT,
        )
        times.append(time.perf_counter() - start)

    avg = sum(times) / len(times)
    assert avg < COLD_START_IMPORT_MAX_S, (
        f"cold start avg={avg:.2f}s > {COLD_START_IMPORT_MAX_S}s"
    )
