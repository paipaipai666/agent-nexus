"""Evaluation API routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(tags=["eval"])


class CompareRequest(BaseModel):
    baseline: str
    candidate: str


@router.get("/datasets")
def list_datasets():
    from pathlib import Path

    from agentnexus.core.config import get_settings

    settings = get_settings()
    evals_dir = Path(settings.traces_dir).parent / "tests" / "evals"
    if not evals_dir.exists():
        evals_dir = Path("tests/evals")
    datasets = []
    if evals_dir.exists():
        for f in evals_dir.glob("*.jsonl"):
            count = sum(1 for line in f.read_text(encoding="utf-8").splitlines() if line.strip())
            datasets.append({"name": f.stem, "filename": f.name, "samples": count})
    return {"datasets": datasets}


@router.post("/run")
def run_eval(quick: bool = True, top_k: int = 3):
    try:
        from agentnexus.rag.eval_dataset import EVAL_SAMPLES, KNOWLEDGE_BASE
        from agentnexus.rag.evaluator import RAGEvaluator

        evaluator = RAGEvaluator(KNOWLEDGE_BASE, EVAL_SAMPLES)
        if quick:
            results = evaluator.run_combination(
                strategy="recursive", chunk_size=512, overlap=64,
                use_hybrid=True, top_k=top_k, max_workers=1, verbose=False,
            )
            return {"status": "ok", "results": results if isinstance(results, dict) else str(results)}
        return {"status": "ok", "message": "Full eval triggered (async not yet implemented)"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/reports")
def list_reports():
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
            reports.append({"filename": f.name, **data})
        except Exception:
            continue
    return {"reports": reports[:20]}


@router.post("/compare")
def compare_reports(req: CompareRequest):
    import json
    from pathlib import Path

    from agentnexus.core.config import get_settings

    settings = get_settings()
    evals_dir = Path(settings.traces_dir) / "evals"

    baseline_path = evals_dir / req.baseline
    candidate_path = evals_dir / req.candidate

    if not baseline_path.exists():
        raise HTTPException(status_code=404, detail=f"Baseline not found: {req.baseline}")
    if not candidate_path.exists():
        raise HTTPException(status_code=404, detail=f"Candidate not found: {req.candidate}")

    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))

    delta = {}
    b_metrics = baseline.get("metrics", {})
    c_metrics = candidate.get("metrics", {})
    for key in set(b_metrics.keys()) | set(c_metrics.keys()):
        b_val = b_metrics.get(key, 0)
        c_val = c_metrics.get(key, 0)
        if isinstance(b_val, (int, float)) and isinstance(c_val, (int, float)):
            delta[key] = {"baseline": b_val, "candidate": c_val, "diff": round(c_val - b_val, 4)}

    return {"baseline": req.baseline, "candidate": req.candidate, "delta": delta}
