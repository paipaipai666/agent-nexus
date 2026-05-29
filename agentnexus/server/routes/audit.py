"""Audit log API routes."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["audit"])


@router.get("")
def list_audit_entries(tool: str | None = None, limit: int = 50):
    from agentnexus.observability.audit_log import get_audit_log

    log = get_audit_log()
    entries = log.entries() if hasattr(log, "entries") else []
    if hasattr(log, "list"):
        entries = log.list(limit=limit, tool=tool)
    elif hasattr(log, "_entries"):
        entries = log._entries

    results = []
    for e in entries:
        if hasattr(e, "__dict__"):
            entry = e.__dict__
        elif isinstance(e, dict):
            entry = e
        else:
            entry = {"raw": str(e)}
        if tool and entry.get("tool_name") != tool:
            continue
        results.append(entry)
        if len(results) >= limit:
            break

    return {"entries": results[:limit], "count": len(results)}
