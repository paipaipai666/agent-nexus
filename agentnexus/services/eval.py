"""Evaluation service facade — 完整的评估服务层。

提供:
  - 任务管理 (list/load/validate)
  - 套件管理 (list/load/run)
  - Baseline 管理 (save/load/compare)
  - 报告管理 (list/generate)
  - RAG 评估 (保留原有功能)
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from agentnexus.evaluation.baseline import BaselineManager
    from agentnexus.evaluation.dataset import EvalDataset
    from agentnexus.evaluation.harness import EvalHarness
    from agentnexus.evaluation.task import EvalTask

logger = logging.getLogger(__name__)


def get_eval_service(module_host=None):
    """Resolve the evaluation implementation selected by the application."""
    from agentnexus.core.modules import get_module_host

    return (module_host or get_module_host()).require("evaluation")


class EvalService:
    """评估服务 — CLI 和 GUI 的共享后端。"""

    def __init__(self, settings: Any | None = None):
        self.settings = settings
        self._dataset: EvalDataset | None = None
        self._harness: EvalHarness | None = None
        self._baseline_mgr: BaselineManager | None = None

    @property
    def traces_dir(self) -> str:
        if self.settings:
            return getattr(self.settings, "traces_dir", str(Path.home() / ".agentnexus" / "traces"))
        return str(Path.home() / ".agentnexus" / "traces")

    @property
    def dataset(self) -> EvalDataset:
        if self._dataset is None:
            from agentnexus.evaluation.dataset import EvalDataset

            self._dataset = EvalDataset()
        return self._dataset

    @property
    def harness(self) -> EvalHarness:
        if self._harness is None:
            from agentnexus.evaluation.harness import EvalHarness, HarnessConfig

            config = HarnessConfig(
                traces_dir=self.traces_dir,
                default_n_trials=1,
                max_concurrency=4,
                collect_transcripts=True,
                scoring_mode="weighted",
            )
            self._harness = EvalHarness(config)
        return self._harness

    @property
    def baseline_mgr(self) -> BaselineManager:
        if self._baseline_mgr is None:
            from agentnexus.evaluation.baseline import BaselineManager

            self._baseline_mgr = BaselineManager(self.traces_dir)
        return self._baseline_mgr

    def list_tasks(
        self,
        category: str | None = None,
        difficulty: str | None = None,
        eval_type: str | None = None,
        tags: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """列出所有 task（可过滤）。"""
        tasks = self.dataset.filter_tasks(category, difficulty, eval_type, tags)
        return [
            {
                "id": t.id,
                "description": t.description,
                "category": t.category,
                "difficulty": t.difficulty,
                "eval_type": t.eval_type,
                "tags": t.tags,
                "grader_count": len(t.graders),
            }
            for t in tasks
        ]

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        """获取单个 task 详情。"""
        task = self.dataset.load_task(task_id)
        if task is None:
            return None
        return task.to_dict()

    def validate_dataset(self) -> dict[str, Any]:
        """验证数据集完整性。"""
        errors = self.dataset.validate()
        stats = self.dataset.stats()
        return {
            "valid": len(errors) == 0,
            "errors": errors,
            "stats": stats,
        }

    def list_suites(self) -> list[dict[str, Any]]:
        """列出所有套件。"""
        return self.dataset.list_suites()

    def get_suite(self, suite_name: str) -> dict[str, Any] | None:
        """获取套件详情。"""
        suite = self.dataset.load_suite(suite_name)
        if suite is None:
            return None
        return suite.to_dict()

    def run_suite(
        self,
        suite_name: str,
        agent_runner: Callable | None = None,
        n_trials: int = 1,
        concurrency: int = 4,
    ) -> dict[str, Any]:
        """运行评估套件。

        Args:
            suite_name: 套件名称
            agent_runner: agent 运行器 (None 时使用默认 mock)
            n_trials: 每个 task 的 trial 数
            concurrency: 并发数

        Returns:
            SuiteReport.to_dict()
        """
        suite = self.dataset.load_suite(suite_name)
        if suite is None:
            raise ValueError(f"Suite not found: {suite_name}")

        if agent_runner is None:
            agent_runner = self._default_agent_runner

        report = self.harness.run_suite(suite, agent_runner, n_trials, concurrency)
        return report.to_dict()

    def run_task(
        self,
        task_id: str,
        agent_runner: Callable | None = None,
        n_trials: int = 1,
    ) -> dict[str, Any]:
        """运行单个 task。"""
        task = self.dataset.load_task(task_id)
        if task is None:
            raise ValueError(f"Task not found: {task_id}")

        if agent_runner is None:
            agent_runner = self._default_agent_runner

        report = self.harness.run_task(task, agent_runner, n_trials)
        return report.to_dict()

    def save_baseline(self, suite_name: str, report: dict[str, Any]) -> str:
        """保存 baseline。"""
        path = self.baseline_mgr.save_baseline(suite_name, report)
        return str(path)

    def load_baseline(self, suite_name: str) -> dict[str, Any] | None:
        """加载 baseline。"""
        return self.baseline_mgr.load_baseline(suite_name)

    def list_baselines(self) -> list[dict[str, Any]]:
        """列出所有 baseline。"""
        return self.baseline_mgr.list_baselines()

    def compare_with_baseline(
        self,
        suite_name: str,
        current_report: dict[str, Any],
    ) -> dict[str, Any]:
        """与 baseline 对比。"""
        baseline = self.baseline_mgr.load_baseline(suite_name)
        if baseline is None:
            return {"error": f"No baseline found for suite: {suite_name}"}
        regression = self.baseline_mgr.compare(current_report, baseline)
        return regression.to_dict()

    def get_eval_stats(self) -> dict[str, Any]:
        """获取评估系统统计信息。"""
        dataset_stats = self.dataset.stats()
        suites = self.dataset.list_suites()
        baselines = self.baseline_mgr.list_baselines()
        return {
            "dataset": dataset_stats,
            "suites": len(suites),
            "baselines": len(baselines),
        }

    def _default_agent_runner(self, task: EvalTask, trial_index: int) -> Any:
        """默认 agent runner — 使用 ReActAgentRunner 执行真实 agent。"""
        from agentnexus.evaluation.runner import ReActAgentRunner

        runner = ReActAgentRunner(self.settings)
        return runner(task, trial_index)

    def list_datasets(self) -> list[dict]:
        """List JSONL evaluation datasets and their sample counts."""
        evals_dir = Path(self.traces_dir).parent / "tests" / "evals"
        if not evals_dir.exists():
            evals_dir = Path("tests/evals")
        if not evals_dir.exists():
            return []
        return [
            {"name": path.stem, "filename": path.name,
             "samples": sum(bool(line.strip()) for line in path.read_text(encoding="utf-8").splitlines())}
            for path in evals_dir.glob("*.jsonl")
        ]

    def run_rag(self, quick: bool = True, top_k: int = 3) -> dict:
        """Run the standard RAG evaluation preset."""
        from agentnexus.rag.eval_dataset import EVAL_SAMPLES, KNOWLEDGE_BASE
        from agentnexus.rag.evaluator import RAGEvaluator

        evaluator = RAGEvaluator(KNOWLEDGE_BASE, EVAL_SAMPLES)
        if not quick:
            return {"status": "ok", "message": "Full eval triggered (async not yet implemented)"}
        results = evaluator.run_combination(
            strategy="recursive", chunk_size=512, overlap=64,
            use_hybrid=True, top_k=top_k, max_workers=1, verbose=False,
        )
        return {"status": "ok", "results": results if isinstance(results, dict) else str(results)}

    def list_reports(self) -> list[dict]:
        """Read the latest persisted evaluation reports."""
        import json

        reports = []
        for path in sorted((Path(self.traces_dir) / "evals").glob("eval_report_*.json"), reverse=True):
            try:
                reports.append({"filename": path.name, **json.loads(path.read_text(encoding="utf-8"))})
            except (OSError, ValueError, TypeError):
                continue
        return reports[:20]

    def compare_reports(self, baseline: str, candidate: str) -> dict:
        """Compare metric values in two named persisted reports."""
        import json

        directory = (Path(self.traces_dir) / "evals").resolve()
        paths = []
        for name in (baseline, candidate):
            if ".." in name or "/" in name or "\\" in name:
                raise ValueError(f"Invalid filename: {name}")
            path = (directory / name).resolve()
            if not path.is_relative_to(directory):
                raise ValueError("Report path outside evals directory")
            if not path.exists():
                raise FileNotFoundError(f"Report not found: {name}")
            paths.append(path)
        first, second = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
        delta = {}
        b_metrics, c_metrics = first.get("metrics", {}), second.get("metrics", {})
        for key in b_metrics.keys() | c_metrics.keys():
            b_value, c_value = b_metrics.get(key, 0), c_metrics.get(key, 0)
            if isinstance(b_value, (int, float)) and isinstance(c_value, (int, float)):
                delta[key] = {"baseline": b_value, "candidate": c_value, "diff": round(c_value - b_value, 4)}
        return {"baseline": baseline, "candidate": candidate, "delta": delta}

    def list_benchmark_suites(self) -> list[dict]:
        """Describe the retrieval benchmark suites."""
        from agentnexus.eval.benchmarks import list_suites

        return [
            {"name": suite.name, "description": suite.description, "datasets": [ref.display for ref in suite.datasets]}
            for suite in list_suites()
        ]

    def run_benchmark(self, suite: str = "multihop", mode: str = "dense", datasets=(),
                      limit: int = 0, top_k: int = 10, embedding_model: str = "") -> dict:
        """Execute a retrieval benchmark using the selected evaluation service."""
        from agentnexus.eval.benchmarks import get_suite, load_suite_datasets, run_retrieval
        from agentnexus.eval.benchmarks.schema import RETRIEVAL_MODES

        if mode not in RETRIEVAL_MODES:
            raise ValueError(f"Unknown mode '{mode}'")
        selected = get_suite(suite)
        loaded = load_suite_datasets(selected, only=tuple(datasets), offline=False)
        settings, original = None, None
        if embedding_model:
            from agentnexus.core.config import get_settings
            from agentnexus.rag.embeddings import reset_embedding_model

            settings = self.settings or get_settings()
            original = settings.embedding_model
            if original != embedding_model:
                settings.embedding_model = embedding_model
                reset_embedding_model()
        try:
            results = []
            for item in loaded:
                run = run_retrieval(item.data, mode=mode, depth=max(100, top_k), k=top_k)
                results.append({
                    "dataset": run.dataset, "mode": run.mode, "embedding_model": run.embedding_model,
                    "n_docs": run.n_docs, "n_queries": run.n_queries, "metrics": run.metrics,
                    "elapsed_s": round(run.elapsed_s, 1),
                })
            return {"suite": selected.name, "results": results}
        finally:
            if settings is not None and settings.embedding_model != original:
                from agentnexus.rag.embeddings import reset_embedding_model

                settings.embedding_model = original
                reset_embedding_model()

    def list_benchmark_reports(self) -> list[dict]:
        """Summarize the latest retrieval benchmark reports."""
        import json

        reports = []
        for path in sorted((Path(self.traces_dir) / "evals").glob("benchmark-*.json"), reverse=True)[:20]:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                first = (payload.get("results") or [{}])[0]
                reports.append({
                    "file": path.name, "suite": payload.get("suite"),
                    "kind": payload.get("kind", "retrieval-benchmark"),
                    "datasets": [result.get("dataset") for result in payload.get("results", [])],
                    "run_at": payload.get("run_at"), "ndcg_at_10": first.get("metrics", {}).get("ndcg@10"),
                })
            except (OSError, ValueError, TypeError):
                continue
        return reports


    def cli_eval_agent(self, days: int=7):
        from agentnexus.cli.eval.agent import _default_eval_agent

        return _default_eval_agent(days)

    def cli_benchmark_list(self):
        from agentnexus.cli.eval.benchmark import _default_benchmark_list

        return _default_benchmark_list()

    def cli_benchmark_run(self, suite: str='beir-lite', datasets: str='', mode: str='dense', depth: int=100, k: int=10, embedding_model: str='', offline: bool=False, ci: bool=False, report: str=''):
        from agentnexus.cli.eval.benchmark import _default_benchmark_run

        return _default_benchmark_run(suite, datasets, mode, depth, k, embedding_model, offline, ci, report)

    def cli_benchmark_run_rgb(self, language: str='zh', task: str='noise', model: str='', limit: int=50, passage_num: int=5, top_k: int=5, mode: str='dense', reinforce_refusal: bool=False, rpm: float=18.0, offline: bool=False, judge_only: str='', generate_only: bool=False, embedding_model: str='', ci: bool=False, report: str=''):
        from agentnexus.cli.eval.benchmark import _default_benchmark_run_rgb

        return _default_benchmark_run_rgb(language, task, model, limit, passage_num, top_k, mode, reinforce_refusal, rpm, offline, judge_only, generate_only, embedding_model, ci, report)

    def cli_benchmark_gate(self, suite: str='multihop', metric: str='ndcg@10', min_value: float=..., report: str=''):
        from agentnexus.cli.eval.benchmark import _default_benchmark_gate

        return _default_benchmark_gate(suite, metric, min_value, report)

    def cli_benchmark_run_multihop(self, model: str='agnes/agnes-3.0-flash', limit: int=0, top_k: int=5, rpm: float=18.0, generate_only: bool=False, judge_only: str='', embedding_model: str='BAAI/bge-small-en-v1.5', offline: bool=False, ci: bool=False, report: str=''):
        from agentnexus.cli.eval.benchmark import _default_benchmark_run_multihop

        return _default_benchmark_run_multihop(model, limit, top_k, rpm, generate_only, judge_only, embedding_model, offline, ci, report)

    def cli_eval_calibrate(self, output: str='./calibrate_samples.json', score_file: str=''):
        from agentnexus.cli.eval.calibrate import _default_eval_calibrate

        return _default_eval_calibrate(output, score_file)

    def cli_eval_humaneval(self, dataset: str='', trace_id: str=''):
        from agentnexus.cli.eval.code_bench import _default_eval_humaneval

        return _default_eval_humaneval(dataset, trace_id)

    def cli_eval_swebench(self, dataset: str=''):
        from agentnexus.cli.eval.code_bench import _default_eval_swebench

        return _default_eval_swebench(dataset)

    def cli_eval_fine_grained(self, scope: str='full', judge: bool=False, json_out: str=''):
        from agentnexus.cli.eval.fine_grained import _default_eval_fine_grained

        return _default_eval_fine_grained(scope, judge, json_out)

    def cli_eval_memory(self, ci: bool=False, judge: bool=False):
        from agentnexus.cli.eval.memory import _default_eval_memory

        return _default_eval_memory(ci, judge)

    def cli_eval_memory_audit(self, db: str | None=None, sample_csv: str | None=None, n_per_category: int=10):
        from agentnexus.cli.eval.memory import _default_eval_memory_audit

        return _default_eval_memory_audit(db, sample_csv, n_per_category)

    def cli_memory_bench_list(self):
        from agentnexus.cli.eval.memory_bench import _default_memory_bench_list

        return _default_memory_bench_list()

    def cli_memory_bench_convert(self, source: str=..., output: str=..., offline: bool=False):
        from agentnexus.cli.eval.memory_bench import _default_memory_bench_convert

        return _default_memory_bench_convert(source, output, offline)

    def cli_memory_bench_rejudge(self, input_report: str=..., output: str='', skip_judged: bool=False):
        from agentnexus.cli.eval.memory_bench import _default_memory_bench_rejudge

        return _default_memory_bench_rejudge(input_report, output, skip_judged)

    def cli_memory_bench_run(self, backend: str='nexus', dataset: str='sample', judge: bool=False, limit: int=0, min_f1: float=0.0, report: str='', ci: bool=False):
        from agentnexus.cli.eval.memory_bench import _default_memory_bench_run

        return _default_memory_bench_run(backend, dataset, judge, limit, min_f1, report, ci)

    def cli_eval_list(self):
        from agentnexus.cli.eval.rag import _default_eval_list

        return _default_eval_list()

    def cli_eval_run(self, ci: bool=False, top_k: int=10, dataset: str='', output: str='', export_format: str='json', quick: bool=False, parallel: bool=False, jobs: int=8, verbose: bool=False, timeout: int=120):
        from agentnexus.cli.eval.rag import _default_eval_run

        return _default_eval_run(ci, top_k, dataset, output, export_format, quick, parallel, jobs, verbose, timeout)

    def cli_eval_history(self):
        from agentnexus.cli.eval.rag import _default_eval_history

        return _default_eval_history()

    def cli_eval_compare(self, baseline: str=..., candidate: str=...):
        from agentnexus.cli.eval.rag import _default_eval_compare

        return _default_eval_compare(baseline, candidate)

    def cli_task_list(self, category: str | None=None, difficulty: str | None=None, eval_type: str | None=None, json_output: bool=False) -> None:
        from agentnexus.cli.eval.task import _default_task_list

        return _default_task_list(category, difficulty, eval_type, json_output)

    def cli_task_show(self, task_id: str=None) -> None:
        from agentnexus.cli.eval.task import _default_task_show

        return _default_task_show(task_id)

    def cli_task_validate(self) -> None:
        from agentnexus.cli.eval.task import _default_task_validate

        return _default_task_validate()

    def cli_task_run(self, task_id: str=None, trials: int=1, json_output: bool=False) -> None:
        from agentnexus.cli.eval.task import _default_task_run

        return _default_task_run(task_id, trials, json_output)

    def cli_suite_list(self, json_output: bool=False) -> None:
        from agentnexus.cli.eval.task import _default_suite_list

        return _default_suite_list(json_output)

    def cli_suite_run(self, suite_name: str=None, trials: int=1, concurrency: int=4, ci: bool=False, json_output: bool=False) -> None:
        from agentnexus.cli.eval.task import _default_suite_run

        return _default_suite_run(suite_name, trials, concurrency, ci, json_output)

    def cli_suite_show(self, suite_name: str=None) -> None:
        from agentnexus.cli.eval.task import _default_suite_show

        return _default_suite_show(suite_name)

    def cli_baseline_list(self) -> None:
        from agentnexus.cli.eval.task import _default_baseline_list

        return _default_baseline_list()

    def cli_baseline_save(self, suite_name: str=None) -> None:
        from agentnexus.cli.eval.task import _default_baseline_save

        return _default_baseline_save(suite_name)

    def cli_baseline_compare(self, suite_name: str=None) -> None:
        from agentnexus.cli.eval.task import _default_baseline_compare

        return _default_baseline_compare(suite_name)

    def cli_eval_trajectory(self, trace_id: str='', days: int=7):
        from agentnexus.cli.eval.trace import _default_eval_trajectory

        return _default_eval_trajectory(trace_id, days)

    def cli_eval_ci(self, days: int=1):
        from agentnexus.cli.eval.trace import _default_eval_ci

        return _default_eval_ci(days)

    def cli_eval_component(self):
        from agentnexus.cli.eval.trace import _default_eval_component

        return _default_eval_component()

    def cli_eval_hallucination(self, trace_id: str=''):
        from agentnexus.cli.eval.trace import _default_eval_hallucination

        return _default_eval_hallucination(trace_id)

    def cli_eval_tool_selection(self):
        from agentnexus.cli.eval.trace import _default_eval_tool_selection

        return _default_eval_tool_selection()

    def cli_eval_coherence(self, trace_id: str=''):
        from agentnexus.cli.eval.trace import _default_eval_coherence

        return _default_eval_coherence(trace_id)

    def cli_transcript_show(self, trace_id: str=None, grader: bool=False, json_output: bool=False) -> None:
        from agentnexus.cli.eval.transcript import _default_transcript_show

        return _default_transcript_show(trace_id, grader, json_output)

    def cli_transcript_list(self, days: int=1, limit: int=20, tool: str | None=None, json_output: bool=False) -> None:
        from agentnexus.cli.eval.transcript import _default_transcript_list

        return _default_transcript_list(days, limit, tool, json_output)

    def cli_transcript_search(self, tool: str | None=None, keyword: str | None=None, days: int=7, limit: int=10) -> None:
        from agentnexus.cli.eval.transcript import _default_transcript_search

        return _default_transcript_search(tool, keyword, days, limit)

    def cli_transcript_failures(self, days: int=1, limit: int=10) -> None:
        from agentnexus.cli.eval.transcript import _default_transcript_failures

        return _default_transcript_failures(days, limit)
