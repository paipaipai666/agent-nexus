"""Baseline 管理 — 存储和对比 regression 基准。

对应 Anthropic 方法论:
  - "capability evals with high pass rates can graduate to become a regression suite"
  - "a decline in score signals that something is broken"
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class BaselineManager:
    """管理 regression 基准线。

    存储结构:
        {traces_dir}/baselines/{suite_name}/
            latest.json          # 最新 baseline
            history/
                {timestamp}.json  # 历史 baseline
    """

    def __init__(self, traces_dir: str | Path):
        self._baselines_dir = Path(traces_dir) / "baselines"
        self._baselines_dir.mkdir(parents=True, exist_ok=True)

    def save_baseline(self, suite_name: str, report: dict[str, Any]) -> Path:
        """保存 baseline。

        Args:
            suite_name: 套件名称
            report: SuiteReport.to_dict() 的输出

        Returns:
            保存的文件路径
        """
        suite_dir = self._baselines_dir / suite_name
        suite_dir.mkdir(parents=True, exist_ok=True)
        history_dir = suite_dir / "history"
        history_dir.mkdir(exist_ok=True)

        # 添加时间戳
        report["baseline_timestamp"] = datetime.now().isoformat()

        # 保存到 history
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        history_path = history_dir / f"{timestamp}.json"
        with open(history_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

        # 更新 latest
        latest_path = suite_dir / "latest.json"
        with open(latest_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

        logger.info("Saved baseline for suite '%s' to %s", suite_name, latest_path)
        return latest_path

    def load_baseline(self, suite_name: str) -> dict[str, Any] | None:
        """加载最新 baseline。"""
        latest_path = self._baselines_dir / suite_name / "latest.json"
        if not latest_path.exists():
            return None
        try:
            with open(latest_path, encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error("Failed to load baseline for '%s': %s", suite_name, e)
            return None

    def list_baselines(self) -> list[dict[str, Any]]:
        """列出所有 baseline。"""
        result: list[dict[str, Any]] = []
        if not self._baselines_dir.exists():
            return result
        for suite_dir in sorted(self._baselines_dir.iterdir()):
            if not suite_dir.is_dir():
                continue
            latest_path = suite_dir / "latest.json"
            if latest_path.exists():
                try:
                    with open(latest_path, encoding="utf-8") as f:
                        data = json.load(f)
                    result.append({
                        "suite_name": suite_dir.name,
                        "timestamp": data.get("baseline_timestamp", "unknown"),
                        "pass_rate": data.get("aggregate", {}).get("pass_rate", 0),
                        "avg_score": data.get("aggregate", {}).get("avg_score", 0),
                    })
                except Exception:
                    continue
        return result

    def list_history(self, suite_name: str) -> list[dict[str, Any]]:
        """列出某个 suite 的 baseline 历史。"""
        history_dir = self._baselines_dir / suite_name / "history"
        if not history_dir.exists():
            return []
        result: list[dict[str, Any]] = []
        for path in sorted(history_dir.iterdir(), reverse=True):
            if path.suffix == ".json":
                try:
                    with open(path, encoding="utf-8") as f:
                        data = json.load(f)
                    result.append({
                        "filename": path.name,
                        "timestamp": data.get("baseline_timestamp", "unknown"),
                        "pass_rate": data.get("aggregate", {}).get("pass_rate", 0),
                        "avg_score": data.get("aggregate", {}).get("avg_score", 0),
                    })
                except Exception:
                    continue
        return result

    def compare(
        self,
        current: dict[str, Any],
        baseline: dict[str, Any],
    ) -> RegressionReport:
        """对比当前结果与 baseline。"""
        current_agg = current.get("aggregate", {})
        baseline_agg = baseline.get("aggregate", {})

        pass_rate_diff = current_agg.get("pass_rate", 0) - baseline_agg.get("pass_rate", 0)
        avg_score_diff = current_agg.get("avg_score", 0) - baseline_agg.get("avg_score", 0)

        # 逐 task 对比
        task_deltas: list[dict[str, Any]] = []
        baseline_tasks = {
            r.get("task_id", ""): r
            for r in baseline.get("task_reports", [])
        }

        regressions: list[str] = []
        improvements: list[str] = []

        for task_report in current.get("task_reports", []):
            task_id = task_report.get("task_id", "")
            base_task = baseline_tasks.get(task_id)
            if not base_task:
                continue

            curr_rate = task_report.get("n_trials", 0)
            curr_passed = sum(1 for t in task_report.get("trials", []) if t.get("passed"))
            base_passed = sum(1 for t in base_task.get("trials", []) if t.get("passed"))

            if curr_passed < base_passed:
                regressions.append(task_id)
            elif curr_passed > base_passed:
                improvements.append(task_id)

            task_deltas.append({
                "task_id": task_id,
                "current_passed": curr_passed,
                "baseline_passed": base_passed,
                "current_avg_score": task_report.get("avg_score", 0),
                "baseline_avg_score": base_task.get("avg_score", 0),
            })

        return RegressionReport(
            suite_name=current.get("suite_name", ""),
            pass_rate_diff=pass_rate_diff,
            avg_score_diff=avg_score_diff,
            regressions=regressions,
            improvements=improvements,
            task_deltas=task_deltas,
            has_regression=len(regressions) > 0,
        )


class RegressionReport:
    """回归检测报告。"""

    def __init__(
        self,
        suite_name: str,
        pass_rate_diff: float,
        avg_score_diff: float,
        regressions: list[str],
        improvements: list[str],
        task_deltas: list[dict[str, Any]],
        has_regression: bool,
    ):
        self.suite_name = suite_name
        self.pass_rate_diff = pass_rate_diff
        self.avg_score_diff = avg_score_diff
        self.regressions = regressions
        self.improvements = improvements
        self.task_deltas = task_deltas
        self.has_regression = has_regression

    def to_dict(self) -> dict[str, Any]:
        return {
            "suite_name": self.suite_name,
            "pass_rate_diff": self.pass_rate_diff,
            "avg_score_diff": self.avg_score_diff,
            "regressions": self.regressions,
            "improvements": self.improvements,
            "has_regression": self.has_regression,
            "task_deltas": self.task_deltas,
        }

    def summary(self) -> str:
        lines = [
            f"Regression Report: {self.suite_name}",
            f"Pass Rate Change: {self.pass_rate_diff:+.1%}",
            f"Score Change: {self.avg_score_diff:+.3f}",
        ]
        if self.regressions:
            lines.append(f"REGRESSIONS ({len(self.regressions)}): {', '.join(self.regressions)}")
        if self.improvements:
            lines.append(f"Improvements ({len(self.improvements)}): {', '.join(self.improvements)}")
        if not self.has_regression:
            lines.append("No regressions detected.")
        return "\n".join(lines)
