"""Stats and logs API routes."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["stats"])


@router.get("/stats")
def get_stats(days: int = 7):
    from agentnexus.core.config import get_settings
    from agentnexus.observability.alerting import evaluate_stats
    from agentnexus.observability.stats import compute_stats

    settings = get_settings()
    stats = compute_stats(settings.traces_dir, days)
    evaluate_stats(stats)
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


@router.get("/eval/reports")
def list_eval_reports():
    import json
    from pathlib import Path

    from agentnexus.core.config import get_settings

    settings = get_settings()
    evals_dir = Path(settings.traces_dir) / "evals"
    if not evals_dir.exists():
        return {"reports": []}

    reports = []
    for f in sorted(evals_dir.glob("eval_report_*.json"), reverse=True):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            reports.append({
                "filename": f.name,
                "created_at": data.get("created_at", ""),
                "strategy": data.get("strategy", ""),
                "chunk_size": data.get("chunk_size", 0),
                "metrics": data.get("metrics", {}),
            })
        except Exception:
            continue

    return {"reports": reports[:20]}
