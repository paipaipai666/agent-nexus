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
from typing import Any, Callable

from agentnexus.evaluation.baseline import BaselineManager
from agentnexus.evaluation.dataset import EvalDataset
from agentnexus.evaluation.harness import EvalHarness, HarnessConfig
from agentnexus.evaluation.task import EvalTask

logger = logging.getLogger(__name__)


class EvalService:
    """评估服务 — CLI 和 GUI 的共享后端。"""

    def __init__(self, settings: Any | None = None):
        self.settings = settings
        self._dataset: EvalDataset | None = None
        self._harness: EvalHarness | None = None
        self._baseline_mgr: BaselineManager | None = None

    # ------------------------------------------------------------------
    # Lazy initialization
    # ------------------------------------------------------------------

    @property
    def traces_dir(self) -> str:
        if self.settings:
            return getattr(self.settings, "traces_dir", str(Path.home() / ".agentnexus" / "traces"))
        return str(Path.home() / ".agentnexus" / "traces")

    @property
    def dataset(self) -> EvalDataset:
        if self._dataset is None:
            self._dataset = EvalDataset()
        return self._dataset

    @property
    def harness(self) -> EvalHarness:
        if self._harness is None:
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
            self._baseline_mgr = BaselineManager(self.traces_dir)
        return self._baseline_mgr

    # ------------------------------------------------------------------
    # Task Management
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Suite Management
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Baseline Management
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # RAG Evaluation (保留原有功能)
    # ------------------------------------------------------------------

    def run_rag_eval(self, *args: Any, **kwargs: Any) -> Any:
        from agentnexus.rag.evaluator import RAGEvaluator

        return RAGEvaluator(*args, **kwargs)

    def list_reports(self) -> list[Path]:
        traces_dir = Path(self.traces_dir)
        if not traces_dir.exists():
            return []
        return sorted(traces_dir.glob("*.jsonl"))

    def compare_reports(self, left: str | Path, right: str | Path) -> dict[str, str]:
        return {"left": str(left), "right": str(right)}

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Default Agent Runner
    # ------------------------------------------------------------------

    def _default_agent_runner(self, task: EvalTask, trial_index: int) -> Any:
        """默认 agent runner — 使用 ReActAgentRunner 执行真实 agent。"""
        from agentnexus.evaluation.graders import ReActAgentRunner

        runner = ReActAgentRunner(self.settings)
        return runner(task, trial_index)
