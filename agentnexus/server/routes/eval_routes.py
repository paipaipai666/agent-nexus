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

from fastapi import APIRouter
from pydantic import BaseModel

from agentnexus.server.error_handlers import APIError

router = APIRouter(tags=["eval"])


class CompareRequest(BaseModel):
    baseline: str
    candidate: str


class RunSuiteRequest(BaseModel):
    n_trials: int = 1
    concurrency: int = 4


class RunTaskRequest(BaseModel):
    n_trials: int = 1


@router.get("/datasets")
def list_datasets():
    return {"datasets": _get_eval_service().list_datasets()}


@router.post("/run")
def run_eval(quick: bool = True, top_k: int = 3):
    service = _get_eval_service()
    try:
        return service.run_rag(quick=quick, top_k=top_k)
    except Exception as exc:
        raise APIError(500, "internal_error", str(exc)) from exc


@router.get("/reports")
def list_reports():
    return {"reports": _get_eval_service().list_reports()}


@router.post("/compare")
def compare_reports(req: CompareRequest):
    service = _get_eval_service()
    try:
        return service.compare_reports(req.baseline, req.candidate)
    except FileNotFoundError as exc:
        raise APIError(404, "not_found", str(exc)) from exc
    except ValueError as exc:
        raise APIError(400, "bad_request", str(exc)) from exc


def _get_eval_service():
    from agentnexus.services.eval import get_eval_service

    return get_eval_service()


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
        raise APIError(404, "not_found", f"Task not found: {task_id}")
    return task


@router.post("/tasks/{task_id}/run")
def run_task(task_id: str, req: RunTaskRequest | None = None):
    service = _get_eval_service()
    try:
        n_trials = req.n_trials if req else 1
        result = service.run_task(task_id, n_trials=n_trials)
        return result
    except ValueError as e:
        raise APIError(404, "not_found", str(e))
    except Exception as e:
        raise APIError(500, "internal_error", str(e))


@router.get("/suites")
def list_suites():
    service = _get_eval_service()
    return {"suites": service.list_suites()}


@router.get("/suites/{suite_name}")
def get_suite(suite_name: str):
    service = _get_eval_service()
    suite = service.get_suite(suite_name)
    if suite is None:
        raise APIError(404, "not_found", f"Suite not found: {suite_name}")
    return suite


@router.post("/suites/{suite_name}/run")
def run_suite(suite_name: str, req: RunSuiteRequest | None = None):
    service = _get_eval_service()
    try:
        n_trials = req.n_trials if req else 1
        concurrency = req.concurrency if req else 4
        result = service.run_suite(suite_name, n_trials=n_trials, concurrency=concurrency)
        return result
    except ValueError as e:
        raise APIError(404, "not_found", str(e))
    except Exception as e:
        raise APIError(500, "internal_error", str(e))


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
    service = _get_eval_service()
    try:
        result = service.run_suite(suite_name)
        path = service.save_baseline(suite_name, result)
        return {"status": "saved", "path": path}
    except Exception as e:
        raise APIError(500, "internal_error", str(e))


@router.post("/suites/{suite_name}/compare")
def compare_with_baseline(suite_name: str):
    service = _get_eval_service()
    try:
        current = service.run_suite(suite_name)
        regression = service.compare_with_baseline(suite_name, current)
        return regression
    except Exception as e:
        raise APIError(500, "internal_error", str(e))


@router.get("/stats")
def eval_stats():
    service = _get_eval_service()
    return service.get_eval_stats()


class BenchmarkRunRequest(BaseModel):
    suite: str = "multihop"
    mode: str = "dense"
    datasets: list[str] = []
    limit: int = 0
    top_k: int = 10
    embedding_model: str = ""


@router.get("/benchmark/suites")
def list_benchmark_suites():
    return {"suites": _get_eval_service().list_benchmark_suites()}


@router.post("/benchmark/run")
def run_benchmark(req: BenchmarkRunRequest | None = None):
    """Run a retrieval benchmark. Retrieval-only — zero LLM calls, safe to
    expose; a full beir-lite pass is minutes, multihop is ~2 minutes."""
    service = _get_eval_service()
    req = req or BenchmarkRunRequest()
    try:
        return service.run_benchmark(**req.model_dump())
    except KeyError as exc:
        raise APIError(404, "not_found", str(exc)) from exc
    except ValueError as exc:
        raise APIError(400, "bad_request", str(exc)) from exc
    except Exception as exc:
        raise APIError(500, "internal_error", str(exc)) from exc


@router.get("/benchmark/reports")
def list_benchmark_reports():
    return {"reports": _get_eval_service().list_benchmark_reports()}
