"""Evaluation API routes — 完整的评估 REST API。

端点:
  GET  /api/eval/datasets      -- 列出 JSONL 数据集
  POST /api/eval/run           -- 运行 RAG 评估
  GET  /api/eval/reports       -- 列出评估报告
  POST /api/eval/compare       -- 对比报告

  GET  /api/eval/tasks         -- 列出 YAML 任务
  GET  /api/eval/tasks/{id}    -- 获取任务详情
  GET  /api/eval/tasks/validate -- 验证数据集
  POST /api/eval/tasks/{id}/run -- 运行单个任务

  GET  /api/eval/suites         -- 列出套件
  GET  /api/eval/suites/{name}  -- 获取套件详情
  POST /api/eval/suites/{name}/run    -- 运行套件
  GET  /api/eval/suites/{name}/baseline -- 获取 baseline
  POST /api/eval/suites/{name}/baseline -- 保存 baseline
  POST /api/eval/suites/{name}/compare  -- 与 baseline 对比

  GET  /api/eval/stats          -- 评估系统统计
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(tags=["eval"])


class CompareRequest(BaseModel):
    baseline: str
    candidate: str


class RunSuiteRequest(BaseModel):
    n_trials: int = 1
    concurrency: int = 4


class RunTaskRequest(BaseModel):
    n_trials: int = 1


# ------------------------------------------------------------------
# Legacy JSONL endpoints (保留兼容)
# ------------------------------------------------------------------

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

    # Reject path traversal attempts
    for name in (req.baseline, req.candidate):
        if ".." in name or "/" in name or "\\" in name:
            raise HTTPException(status_code=400, detail=f"Invalid filename: {name}")

    baseline_path = (evals_dir / req.baseline).resolve()
    candidate_path = (evals_dir / req.candidate).resolve()
    evals_resolved = evals_dir.resolve()

    if not str(baseline_path).startswith(str(evals_resolved)):
        raise HTTPException(status_code=400, detail="Baseline path outside evals directory")
    if not str(candidate_path).startswith(str(evals_resolved)):
        raise HTTPException(status_code=400, detail="Candidate path outside evals directory")

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


# ------------------------------------------------------------------
# New Task/Suite endpoints
# ------------------------------------------------------------------

def _get_eval_service():
    from agentnexus.services.eval import EvalService
    return EvalService()


@router.get("/tasks")
def list_tasks(
    category: str | None = None,
    difficulty: str | None = None,
    eval_type: str | None = None,
):
    service = _get_eval_service()
    return {"tasks": service.list_tasks(category=category, difficulty=difficulty, eval_type=eval_type)}


@router.get("/tasks/validate")
def validate_tasks():
    service = _get_eval_service()
    return service.validate_dataset()


@router.get("/tasks/{task_id}")
def get_task(task_id: str):
    service = _get_eval_service()
    task = service.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    return task


@router.post("/tasks/{task_id}/run")
def run_task(task_id: str, req: RunTaskRequest | None = None):
    try:
        service = _get_eval_service()
        n_trials = req.n_trials if req else 1
        result = service.run_task(task_id, n_trials=n_trials)
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/suites")
def list_suites():
    service = _get_eval_service()
    return {"suites": service.list_suites()}


@router.get("/suites/{suite_name}")
def get_suite(suite_name: str):
    service = _get_eval_service()
    suite = service.get_suite(suite_name)
    if suite is None:
        raise HTTPException(status_code=404, detail=f"Suite not found: {suite_name}")
    return suite


@router.post("/suites/{suite_name}/run")
def run_suite(suite_name: str, req: RunSuiteRequest | None = None):
    try:
        service = _get_eval_service()
        n_trials = req.n_trials if req else 1
        concurrency = req.concurrency if req else 4
        result = service.run_suite(suite_name, n_trials=n_trials, concurrency=concurrency)
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/suites/{suite_name}/baseline")
def get_baseline(suite_name: str):
    service = _get_eval_service()
    baseline = service.load_baseline(suite_name)
    if baseline is None:
        return {"suite_name": suite_name, "exists": False, "message": "No baseline saved yet"}
    baseline["exists"] = True
    return baseline


@router.post("/suites/{suite_name}/baseline")
def save_baseline(suite_name: str):
    try:
        service = _get_eval_service()
        result = service.run_suite(suite_name)
        path = service.save_baseline(suite_name, result)
        return {"status": "saved", "path": path}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/suites/{suite_name}/compare")
def compare_with_baseline(suite_name: str):
    try:
        service = _get_eval_service()
        current = service.run_suite(suite_name)
        regression = service.compare_with_baseline(suite_name, current)
        return regression
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/stats")
def eval_stats():
    service = _get_eval_service()
    return service.get_eval_stats()


# ------------------------------------------------------------------
# Public benchmark endpoints (BEIR / MultiHop-RAG track)
# ------------------------------------------------------------------

class BenchmarkRunRequest(BaseModel):
    suite: str = "multihop"
    mode: str = "dense"
    datasets: list[str] = []
    limit: int = 0
    top_k: int = 10
    embedding_model: str = ""


@router.get("/benchmark/suites")
def list_benchmark_suites():
    from agentnexus.eval.benchmarks import list_suites

    return {
        "suites": [
            {
                "name": s.name,
                "description": s.description,
                "datasets": [ref.display for ref in s.datasets],
            }
            for s in list_suites()
        ]
    }


@router.post("/benchmark/run")
def run_benchmark(req: BenchmarkRunRequest | None = None):
    """Run a retrieval benchmark. Retrieval-only — zero LLM calls, safe to
    expose; a full beir-lite pass is minutes, multihop is ~2 minutes."""
    req = req or BenchmarkRunRequest()
    try:
        from agentnexus.eval.benchmarks import get_suite, load_suite_datasets, run_retrieval
        from agentnexus.eval.benchmarks.schema import RETRIEVAL_MODES

        if req.mode not in RETRIEVAL_MODES:
            raise HTTPException(status_code=400, detail=f"Unknown mode '{req.mode}'")
        suite = get_suite(req.suite)
        loaded = load_suite_datasets(suite, only=tuple(req.datasets), offline=False)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # optional embedding switch for this run
    settings = original = None
    if req.embedding_model:
        from agentnexus.core.config import get_settings
        from agentnexus.rag.embeddings import reset_embedding_model

        settings = get_settings()
        original = settings.embedding_model
        if original != req.embedding_model:
            settings.embedding_model = req.embedding_model
            reset_embedding_model()
    try:
        results = []
        for item in loaded:
            run = run_retrieval(item.data, mode=req.mode, depth=max(100, req.top_k), k=req.top_k)
            results.append({
                "dataset": run.dataset,
                "mode": run.mode,
                "embedding_model": run.embedding_model,
                "n_docs": run.n_docs,
                "n_queries": run.n_queries,
                "metrics": run.metrics,
                "elapsed_s": round(run.elapsed_s, 1),
            })
        return {"suite": suite.name, "results": results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if settings is not None and original and settings.embedding_model != original:
            from agentnexus.rag.embeddings import reset_embedding_model

            settings.embedding_model = original
            reset_embedding_model()


@router.get("/benchmark/reports")
def list_benchmark_reports():
    import json
    from pathlib import Path

    from agentnexus.core.config import get_settings

    report_dir = Path(get_settings().traces_dir) / "evals"
    reports = []
    if report_dir.exists():
        for path in sorted(report_dir.glob("benchmark-*.json"), reverse=True)[:20]:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                first = (payload.get("results") or [{}])[0]
                reports.append({
                    "file": path.name,
                    "suite": payload.get("suite"),
                    "kind": payload.get("kind", "retrieval-benchmark"),
                    "datasets": [r.get("dataset") for r in payload.get("results", [])],
                    "run_at": payload.get("run_at"),
                    "ndcg_at_10": first.get("metrics", {}).get("ndcg@10"),
                })
            except Exception:
                continue
    return {"reports": reports}
