"""Stats and logs API routes."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["stats"])


@router.get("/stats")
def get_stats(days: int = 7):
    from agentnexus.core.config import get_settings
    from agentnexus.observability.stats import compute_stats

    settings = get_settings()
    stats = compute_stats(settings.traces_dir, days)
    if hasattr(stats, "__dict__"):
        return stats.__dict__
    return stats


@router.get("/logs")
def list_logs(days: int = 7):
    import json
    from pathlib import Path

    from agentnexus.core.config import get_settings

    settings = get_settings()
    traces_dir = Path(settings.traces_dir)
    traces = []

    for f in sorted(traces_dir.glob("*.jsonl"), reverse=True):
        if not f.name[0].isdigit():
            continue
        spans = []
        try:
            for line in f.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    spans.append(json.loads(line))
        except Exception:
            continue
        if spans:
            trace_ids = set(s.get("trace_id", "") for s in spans)
            for tid in trace_ids:
                trace_spans = [s for s in spans if s.get("trace_id") == tid]
                traces.append({
                    "trace_id": tid,
                    "date": f.stem,
                    "span_count": len(trace_spans),
                    "spans": trace_spans,
                })

    return {"traces": traces[:50], "total": len(traces)}


@router.get("/logs/{trace_id}")
def get_trace(trace_id: str):
    import json
    from pathlib import Path

    from agentnexus.core.config import get_settings

    settings = get_settings()
    traces_dir = Path(settings.traces_dir)
    spans = []

    for f in traces_dir.glob("*.jsonl"):
        try:
            for line in f.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    span = json.loads(line)
                    if span.get("trace_id") == trace_id:
                        spans.append(span)
        except Exception:
            continue

    return {"trace_id": trace_id, "spans": spans}


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
