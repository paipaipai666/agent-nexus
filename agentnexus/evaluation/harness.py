"""评估 Harness — 端到端评估运行基础设施。

对应 Anthropic 方法论中的 "evaluation harness":
  - 提供 instructions 和 tools
  - 并发运行 tasks
  - 记录所有步骤
  - 评分 outputs
  - 聚合 results
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Callable

from agentnexus.evaluation.graders import (
    create_composite_grader,
)
from agentnexus.evaluation.statistics import (
    compute_pass_metrics,
    compute_saturation_score,
    compute_trial_consistency,
)
from agentnexus.evaluation.task import EvalSuite, EvalTask
from agentnexus.evaluation.trial import AggregateMetrics, TaskReport, TrialResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Harness Config
# ---------------------------------------------------------------------------

@dataclass
class HarnessConfig:
    """Harness 配置。

    Attributes:
        traces_dir: trace 文件存储目录
        default_n_trials: 默认每个 task 运行几次 trial
        max_concurrency: 最大并发数
        default_timeout_sec: 默认超时
        collect_transcripts: 是否收集完整 transcript
        scoring_mode: 默认评分模式 (weighted/binary/hybrid)
        verbose: 是否输出详细日志
    """

    traces_dir: str = ""
    default_n_trials: int = 1
    max_concurrency: int = 4
    default_timeout_sec: int = 120
    collect_transcripts: bool = True
    scoring_mode: str = "weighted"
    verbose: bool = False


# ---------------------------------------------------------------------------
# Agent Runner Protocol
# ---------------------------------------------------------------------------

# AgentRunner 是一个 callable: (task: EvalTask) -> TrialResult
# 由外部注入，harness 不关心具体 agent 实现
AgentRunner = Callable[[EvalTask, int], TrialResult]


# ---------------------------------------------------------------------------
# Eval Harness
# ---------------------------------------------------------------------------

class EvalHarness:
    """端到端评估 harness。

    使用方式:
        harness = EvalHarness(config)
        report = harness.run_suite(suite, agent_runner, n_trials=3)
    """

    def __init__(self, config: HarnessConfig | None = None):
        self._config = config or HarnessConfig()

    @property
    def config(self) -> HarnessConfig:
        return self._config

    # ------------------------------------------------------------------
    # Suite-level execution
    # ------------------------------------------------------------------

    def run_suite(
        self,
        suite: EvalSuite,
        agent_runner: AgentRunner,
        n_trials: int | None = None,
        concurrency: int | None = None,
    ) -> SuiteReport:
        """运行整个评估套件。

        Args:
            suite: 评估套件
            agent_runner: agent 运行器
            n_trials: 每个 task 的 trial 数 (覆盖默认)
            concurrency: 并发数 (覆盖默认)

        Returns:
            SuiteReport
        """
        trials = n_trials or self._config.default_n_trials
        workers = concurrency or self._config.max_concurrency
        start_time = time.monotonic()

        task_reports: list[TaskReport] = []

        if workers > 1 and len(suite.tasks) > 1:
            task_reports = self._run_parallel(suite.tasks, agent_runner, trials, workers)
        else:
            task_reports = self._run_sequential(suite.tasks, agent_runner, trials)

        elapsed_ms = (time.monotonic() - start_time) * 1000

        # 聚合指标
        aggregate = self._compute_aggregate(task_reports, elapsed_ms)

        # 饱和度
        pass_rates = [tr.success_rate for tr in task_reports]
        saturation = compute_saturation_score(pass_rates)

        # 检查是否通过
        passed = self._check_suite_passed(task_reports, suite)

        return SuiteReport(
            suite_name=suite.name,
            eval_type=suite.eval_type,
            task_reports=task_reports,
            aggregate=aggregate,
            saturation=saturation,
            passed=passed,
            duration_ms=elapsed_ms,
        )

    # ------------------------------------------------------------------
    # Task-level execution
    # ------------------------------------------------------------------

    def run_task(
        self,
        task: EvalTask,
        agent_runner: AgentRunner,
        n_trials: int | None = None,
    ) -> TaskReport:
        """运行单个 task 的 N 次 trial。"""
        trials = n_trials or self._config.default_n_trials
        trial_results: list[TrialResult] = []

        for i in range(trials):
            if self._config.verbose:
                logger.info("Running task %s trial %d/%d", task.id, i + 1, trials)
            # 环境隔离: setup
            env_state = self._setup_environment(task)
            try:
                result = agent_runner(task, i)
                # 补充 outcome 中的环境状态
                if env_state:
                    result.outcome.setdefault("environment", env_state)
                # 评分
                result = self._grade_trial(task, result)
                trial_results.append(result)
            except Exception as e:
                logger.error("Task %s trial %d failed: %s", task.id, i, e)
                trial_results.append(TrialResult(
                    task_id=task.id,
                    trial_index=i,
                    error=str(e),
                ))
            finally:
                # 环境隔离: teardown
                self._teardown_environment(task)

        # 计算 pass 指标
        n_success = sum(1 for t in trial_results if t.passed)
        pass_metrics = compute_pass_metrics(len(trial_results), n_success)
        consistency = compute_trial_consistency([t.final_score for t in trial_results])

        report = TaskReport(
            task_id=task.id,
            task_description=task.description,
            trials=trial_results,
            pass_at_k={int(k.replace("pass_at_", "")): v for k, v in pass_metrics.items() if k.startswith("pass_at_") and v is not None},
            pass_hat_k={int(k.replace("pass_hat_", "")): v for k, v in pass_metrics.items() if k.startswith("pass_hat_")},
            avg_score=consistency["mean"],
            score_std=consistency["std"],
            passed=n_success > 0,
        )

        return report

    # ------------------------------------------------------------------
    # Grading
    # ------------------------------------------------------------------

    def _grade_trial(self, task: EvalTask, result: TrialResult) -> TrialResult:
        """对 trial 结果运行所有 graders。"""
        if not task.graders:
            result.passed = bool(result.final_answer)
            result.final_score = 1.0 if result.passed else 0.0
            return result

        composite = create_composite_grader(task.graders, mode=self._config.scoring_mode)
        score = composite.grade(
            task_input=task.input,
            transcript=result.transcript,
            outcome=result.outcome,
        )

        result.grader_scores[composite.name] = score
        result.final_score = score.score
        result.passed = score.passed

        return result

    # ------------------------------------------------------------------
    # Environment isolation
    # ------------------------------------------------------------------

    def _setup_environment(self, task: EvalTask) -> dict[str, Any]:
        """为 trial 创建隔离环境。

        如果 task 定义了 environment，执行 setup_cmd 并返回初始状态。
        """
        import subprocess

        env = task.environment
        if not env:
            return {}

        env_state: dict[str, Any] = {"type": env.get("type", "default")}

        # 执行 setup 命令
        setup_cmd = env.get("setup_cmd")
        if setup_cmd:
            try:
                result = subprocess.run(
                    setup_cmd, shell=True, capture_output=True, text=True, timeout=30,
                )
                env_state["setup_returncode"] = result.returncode
                env_state["setup_stdout"] = result.stdout[:500]
                if result.returncode != 0:
                    logger.warning("Task %s setup failed: %s", task.id, result.stderr[:200])
            except subprocess.TimeoutExpired:
                logger.warning("Task %s setup timed out", task.id)
            except Exception as e:
                logger.warning("Task %s setup error: %s", task.id, e)

        return env_state

    def _teardown_environment(self, task: EvalTask) -> None:
        """trial 结束后清理环境。"""
        import subprocess

        env = task.environment
        if not env:
            return

        teardown_cmd = env.get("teardown_cmd")
        if teardown_cmd:
            try:
                subprocess.run(
                    teardown_cmd, shell=True, capture_output=True, text=True, timeout=10,
                )
            except Exception as e:
                logger.debug("Task %s teardown error: %s", task.id, e)

    # ------------------------------------------------------------------
    # Internal execution
    # ------------------------------------------------------------------

    def _run_sequential(
        self,
        tasks: list[EvalTask],
        agent_runner: AgentRunner,
        n_trials: int,
    ) -> list[TaskReport]:
        """顺序运行。"""
        reports: list[TaskReport] = []
        for task in tasks:
            report = self.run_task(task, agent_runner, n_trials)
            reports.append(report)
        return reports

    def _run_parallel(
        self,
        tasks: list[EvalTask],
        agent_runner: AgentRunner,
        n_trials: int,
        max_workers: int,
    ) -> list[TaskReport]:
        """并发运行。"""
        reports: list[TaskReport] = [None] * len(tasks)  # type: ignore

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_idx = {
                executor.submit(self.run_task, task, agent_runner, n_trials): i
                for i, task in enumerate(tasks)
            }
            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]
                try:
                    reports[idx] = future.result()
                except Exception as e:
                    logger.error("Task %s failed: %s", tasks[idx].id, e)
                    reports[idx] = TaskReport(
                        task_id=tasks[idx].id,
                        task_description=tasks[idx].description,
                    )

        return reports  # type: ignore

    # ------------------------------------------------------------------
    # Aggregation
    # ------------------------------------------------------------------

    def _compute_aggregate(
        self,
        task_reports: list[TaskReport],
        total_duration_ms: float,
    ) -> AggregateMetrics:
        """计算套件级别的聚合指标。"""
        total_tasks = len(task_reports)
        passed_tasks = sum(1 for r in task_reports if r.passed)
        pass_rate = passed_tasks / total_tasks if total_tasks > 0 else 0.0

        all_scores = [r.avg_score for r in task_reports]
        avg_score = sum(all_scores) / len(all_scores) if all_scores else 0.0

        total_trials = sum(len(r.trials) for r in task_reports)
        total_tokens = sum(
            t.metadata.get("n_total_tokens", 0)
            for r in task_reports
            for t in r.trials
        )
        total_cost = sum(
            t.metadata.get("cost", 0.0)
            for r in task_reports
            for t in r.trials
        )

        # 按类别统计
        by_category: dict[str, dict[str, Any]] = {}
        # 按难度统计
        by_difficulty: dict[str, dict[str, Any]] = {}

        # 分数分布
        score_distribution: dict[str, int] = {
            "0.0-0.2": 0, "0.2-0.4": 0, "0.4-0.6": 0, "0.6-0.8": 0, "0.8-1.0": 0,
        }
        for score in all_scores:
            if score < 0.2:
                score_distribution["0.0-0.2"] += 1
            elif score < 0.4:
                score_distribution["0.2-0.4"] += 1
            elif score < 0.6:
                score_distribution["0.4-0.6"] += 1
            elif score < 0.8:
                score_distribution["0.6-0.8"] += 1
            else:
                score_distribution["0.8-1.0"] += 1

        return AggregateMetrics(
            total_tasks=total_tasks,
            passed_tasks=passed_tasks,
            pass_rate=pass_rate,
            avg_score=avg_score,
            score_distribution=score_distribution,
            by_category=by_category,
            by_difficulty=by_difficulty,
            total_trials=total_trials,
            total_duration_ms=total_duration_ms,
            total_tokens=total_tokens,
            total_cost=total_cost,
        )

    def _check_suite_passed(self, task_reports: list[TaskReport], suite: EvalSuite) -> bool:
        """检查套件是否整体通过。"""
        if not task_reports:
            return False

        total = len(task_reports)
        passed = sum(1 for r in task_reports if r.passed)
        pass_rate = passed / total

        # 检查最低通过率
        if pass_rate < suite.thresholds.min_pass_rate:
            return False

        # 检查 saturation
        saturation = compute_saturation_score([r.success_rate for r in task_reports])
        if saturation["saturation"] > suite.thresholds.max_saturation:
            return False  # 已饱和，需要升级

        return True


# ---------------------------------------------------------------------------
# Suite Report
# ---------------------------------------------------------------------------

class SuiteReport:
    """套件级别的评估报告。"""

    def __init__(
        self,
        suite_name: str,
        eval_type: str,
        task_reports: list[TaskReport],
        aggregate: AggregateMetrics,
        saturation: dict[str, Any],
        passed: bool,
        duration_ms: float,
    ):
        self.suite_name = suite_name
        self.eval_type = eval_type
        self.task_reports = task_reports
        self.aggregate = aggregate
        self.saturation = saturation
        self.passed = passed
        self.duration_ms = duration_ms

    def to_dict(self) -> dict[str, Any]:
        return {
            "suite_name": self.suite_name,
            "eval_type": self.eval_type,
            "passed": self.passed,
            "duration_ms": self.duration_ms,
            "aggregate": self.aggregate.to_dict(),
            "saturation": self.saturation,
            "task_reports": [r.to_dict() for r in self.task_reports],
        }

    def summary(self) -> str:
        """人类可读摘要。"""
        lines = [
            f"Suite: {self.suite_name} ({self.eval_type})",
            f"Status: {'PASSED' if self.passed else 'FAILED'}",
            f"Tasks: {self.aggregate.passed_tasks}/{self.aggregate.total_tasks} passed ({self.aggregate.pass_rate:.1%})",
            f"Avg Score: {self.aggregate.avg_score:.2f}",
            f"Trials: {self.aggregate.total_trials}",
            f"Duration: {self.duration_ms:.0f}ms",
        ]
        if self.saturation.get("upgrade_suggestion"):
            lines.append(f"Saturation: {self.saturation['upgrade_suggestion']}")
        return "\n".join(lines)

    def regression_delta(self, baseline: SuiteReport) -> dict[str, Any]:
        """与 baseline 对比。"""
        delta: dict[str, Any] = {
            "pass_rate_diff": self.aggregate.pass_rate - baseline.aggregate.pass_rate,
            "avg_score_diff": self.aggregate.avg_score - baseline.aggregate.avg_score,
            "task_deltas": [],
        }

        baseline_map = {r.task_id: r for r in baseline.task_reports}
        for report in self.task_reports:
            base = baseline_map.get(report.task_id)
            if base:
                delta["task_deltas"].append({
                    "task_id": report.task_id,
                    "pass_rate_diff": report.success_rate - base.success_rate,
                    "avg_score_diff": report.avg_score - base.avg_score,
                })

        return delta
