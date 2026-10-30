"""Stats and logs API routes."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["stats"])


@router.get("/stats")
def get_stats(days: int = 7):
    from agentnexus.core.modules import get_module_host

    observer = get_module_host().require("observability")
    stats = observer.stats(days)
    observer.evaluate_stats(stats)
    if hasattr(stats, "__dict__"):
        return stats.__dict__
    return stats


@router.get("/logs")
def list_logs(days: int = 7):
    """Read daily and rotated trace files within the selected range."""
    from agentnexus.services.inspection import list_logs as inspect_logs

    return inspect_logs(days)


@router.get("/logs/{trace_id}")
def get_trace(trace_id: str):
    """Read a trace through the shared inspection operation."""
    from agentnexus.services.inspection import get_trace as inspect_trace

    return inspect_trace(trace_id)
