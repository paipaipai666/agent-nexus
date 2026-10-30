"""Read runtime logs and audit records for HTTP and terminal clients."""

from dataclasses import asdict, is_dataclass


def trace_spans(days: int | None = None):
    """Yield valid trace spans, preserving rotated daily log files."""
    from agentnexus.core.modules import get_module_host

    yield from get_module_host().require("observability").trace_spans(days)


def list_logs(days: int = 7):
    """Group trace spans for the selected time range."""
    from agentnexus.core.modules import get_module_host

    return get_module_host().require("observability").list_logs(days)


def get_trace(trace_id: str):
    """Read a complete trace without interpreting its ID as a filesystem path."""
    from agentnexus.core.modules import get_module_host

    return get_module_host().require("observability").get_trace(trace_id)


def list_audit_entries(tool: str | None = None, limit: int = 50):
    """Read the audit buffer through its supported public snapshot function."""
    from agentnexus.core.telemetry import get_audit_log

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
