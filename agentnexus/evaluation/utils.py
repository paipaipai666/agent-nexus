"""Shared utilities for reading JSONL trace files."""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Callable, Iterator
from pathlib import Path


def load_trace_spans(filepath: str | Path) -> dict[str, list[dict]]:
    """Load all spans from a JSONL trace file, grouped by ``trace_id``.

    Returns a dict mapping trace_id to a list of span dicts.  Malformed lines
    and JSON decode errors are silently skipped.
    """
    traces: dict[str, list[dict]] = defaultdict(list)
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                span = json.loads(line)
            except json.JSONDecodeError:
                continue
            traces[span.get("trace_id", "unknown")].append(span)
    return dict(traces)


def find_trace_in_file(filepath: str | Path, trace_id: str) -> list[dict] | None:
    """Return spans for *trace_id* from a single JSONL file, or ``None``."""
    spans: list[dict] = []
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                span = json.loads(line)
            except json.JSONDecodeError:
                continue
            if span.get("trace_id") == trace_id:
                spans.append(span)
    return spans if spans else None


def find_trace(traces_dir: str | Path, trace_id: str) -> list[dict] | None:
    """Search *traces_dir* (newest-first) for spans matching *trace_id*."""
    for f in sorted(Path(traces_dir).glob("*.jsonl"), reverse=True):
        spans = find_trace_in_file(f, trace_id)
        if spans:
            return spans
    return None


def iter_spans(
    traces_dir: str | Path,
    *,
    filter_fn: Callable[[dict], bool] | None = None,
) -> Iterator[dict]:
    """Yield spans from all JSONL files in *traces_dir*, optionally filtered."""
    for f in sorted(Path(traces_dir).glob("*.jsonl")):
        with open(f, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    span = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if filter_fn is None or filter_fn(span):
                    yield span


def load_all_traces(traces_dir: str | Path) -> dict[str, list[dict]]:
    """Load all traces from *traces_dir*, grouped by trace_id."""
    grouped: dict[str, list[dict]] = defaultdict(list)
    for span in iter_spans(traces_dir):
        grouped[span.get("trace_id", "unknown")].append(span)
    return dict(grouped)
