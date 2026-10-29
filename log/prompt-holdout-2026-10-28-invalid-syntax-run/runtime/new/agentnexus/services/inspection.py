"""Read runtime logs and audit records for HTTP and terminal clients."""

import json
from dataclasses import asdict, is_dataclass
from datetime import date, timedelta
from pathlib import Path


def trace_spans(days: int | None = None):
    """Yield valid trace spans, preserving rotated daily log files."""
    from agentnexus.core.config import get_settings

    if days is not None and not 1 <= days <= 3650:
        raise ValueError("天数必须在 1 到 3650 之间")
    cutoff = date.today() - timedelta(days=(days or 1) - 1)
    for path in sorted(Path(get_settings().traces_dir).glob("*.jsonl"), reverse=True):
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


def list_logs(days: int = 7):
    """Group trace spans for the selected time range."""
    grouped = {}
    for day, span in trace_spans(days):
        trace_id = span.get("trace_id", "")
        entry = grouped.setdefault(trace_id, {"trace_id": trace_id, "date": day, "span_count": 0, "spans": []})
        entry["spans"].append(span)
        entry["span_count"] += 1
    return {"traces": list(grouped.values())[:50], "total": len(grouped)}


def get_trace(trace_id: str):
    """Read a complete trace without interpreting its ID as a filesystem path."""
    return {"trace_id": trace_id, "spans": [span for _, span in trace_spans() if span.get("trace_id") == trace_id]}


def list_audit_entries(tool: str | None = None, limit: int = 50):
    """Read the audit buffer through its supported public snapshot function."""
    from agentnexus.observability.audit_log import get_audit_log

    if not 1 <= limit <= 1000:
        raise ValueError("数量必须在 1 到 1000 之间")
    entries = []
    for raw in get_audit_log():
        entry = asdict(raw) if is_dataclass(raw) else raw
        if not isinstance(entry, dict):
            entry = {"raw": str(entry)}
        if tool and entry.get("tool_name") != tool:
            continue
        entries.append(entry)
        if len(entries) == limit:
            break
    return {"entries": entries, "count": len(entries)}
