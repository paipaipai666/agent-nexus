"""Read built-in JSONL observations for terminal and HTTP clients."""

import json
import time
from datetime import date, timedelta
from pathlib import Path


def trace_spans(traces_dir: str, days: int | None = None):
    """Yield dated spans, preserving rotated daily trace files."""
    if days is not None and not 1 <= days <= 3650:
        raise ValueError("天数必须在 1 到 3650 之间")
    cutoff = date.today() - timedelta(days=(days or 1) - 1)
    for path in sorted(Path(traces_dir).glob("*.jsonl"), reverse=True):
        try:
            file_date = date.fromisoformat(path.stem[:10])
        except ValueError:
            continue
        if days is not None and file_date < cutoff:
            continue
        try:
            with path.open(encoding="utf-8") as file:
                for line in file:
                    try:
                        span = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(span, dict):
                        yield path.stem, span
        except OSError:
            continue


def read_trace_spans(traces_dir: str, days: int | None = None) -> list[dict]:
    """Read spans using timestamps, including custom trace filenames."""
    cutoff = time.time() - days * 86400 if days is not None else float("-inf")
    spans = []
    for path in sorted(Path(traces_dir).glob("*.jsonl"), reverse=True):
        try:
            with path.open(encoding="utf-8") as file:
                for line in file:
                    try:
                        span = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(span, dict) and span.get("start_time", 0) >= cutoff:
                        spans.append(span)
        except OSError:
            continue
    return spans
