"""Performance: File operations — read, write, list.

Thresholds:
    FILE_READ_SMALL_P95_MAX_MS  = 10    1KB file read < 10ms
    FILE_READ_LARGE_P95_MAX_MS  = 50    100KB file read < 50ms
    FILE_WRITE_10K_P95_MAX_MS   = 30    10KB file write < 30ms
    FILE_LIST_100_P95_MAX_MS    = 50    list 100 files < 50ms
"""

from __future__ import annotations

from pathlib import Path

FILE_READ_SMALL_P95_MAX_MS = 10
FILE_READ_LARGE_P95_MAX_MS = 50
FILE_WRITE_10K_P95_MAX_MS = 30
FILE_LIST_100_P95_MAX_MS = 50


def test_file_read_small(benchmark, perf_env, monkeypatch):
    monkeypatch.chdir(str(perf_env))
    Path("small.txt").write_text("x" * 1024)

    from agentnexus.tools.file_ops import file_read
    result = benchmark(file_read, "small.txt")
    assert "small.txt" in result


def test_file_read_large(benchmark, perf_env, monkeypatch):
    monkeypatch.chdir(str(perf_env))
    Path("large.txt").write_text("x" * 1024 * 100)

    from agentnexus.tools.file_ops import file_read
    result = benchmark(file_read, "large.txt", 0, 100)
    assert "large.txt" in result


def test_file_write_10k(benchmark, perf_env, monkeypatch):
    monkeypatch.chdir(str(perf_env))
    content = "x" * 1024 * 10

    from agentnexus.tools.file_ops import file_write

    def _write():
        p = Path("write_test.txt")
        if p.exists():
            p.unlink()
        return file_write("write_test.txt", content, "create")

    result = benchmark(_write)
    assert result["status"] == "ok"
    assert Path("write_test.txt").stat().st_size == 10240


def test_file_list_100(benchmark, perf_env, monkeypatch):
    monkeypatch.chdir(str(perf_env))
    for i in range(100):
        Path(f"file_{i:04d}.txt").write_text("x")

    from agentnexus.tools.file_ops import file_list
    result = benchmark(file_list, ".", "*.txt")
    assert "100" in result
