"""SWE-bench style evaluator — patch-based issue resolution tests.

Reuses the code execution engine from HumanEval but expects additional
fields (repo, issue_id) in the dataset.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from agentnexus.evaluation.humaneval import HumanEvalEvaluator


@dataclass
class SWEBenchReport:
    """Aggregate report for SWE-bench evaluation."""

    total_problems: int = 0
    resolved: int = 0
    unresolved: list[str] = field(default_factory=list)

    @property
    def resolve_rate(self) -> float:
        if self.total_problems == 0:
            return 0.0
        return self.resolved / self.total_problems

    def summary(self) -> str:
        return (
            f"SWE-bench: {self.total_problems} issues | "
            f"resolve rate: {self.resolve_rate:.1%} | "
            f"unresolved: {len(self.unresolved)}"
        )


class SWEBenchEvaluator:
    """Evaluate patch-based issue resolution against SWE-bench test cases."""

    def __init__(self) -> None:
        self._inner = HumanEvalEvaluator()

    def evaluate_all(
        self,
        dataset_path: str | Path,
        patches: dict[str, str],
    ) -> SWEBenchReport:
        """Evaluate all patches in a dict ``{trace_id: patched_code}``."""
        inner_report = self._inner.evaluate_all(dataset_path, patches)
        report = SWEBenchReport(total_problems=inner_report.total_problems)

        for result in inner_report.results:
            if result.failed == 0 and result.error == "":
                report.resolved += 1
            else:
                report.unresolved.append(result.trace_id)

        return report
