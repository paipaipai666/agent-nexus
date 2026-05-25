"""HumanEval / SWE-bench style code generation evaluator.

Loads JSONL datasets with function signatures, test cases, and expected
solutions.  Designed to score generated code via pass@1 — running test
assertions against a candidate solution.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


@dataclass
class ProblemSample:
    """A single code-generation problem from the dataset."""

    trace_id: str
    question: str
    expected_answer: str
    language: str
    test_cases: list[str]


@dataclass
class CodeGenResult:
    """Result of evaluating one candidate solution."""

    trace_id: str
    passed: int = 0
    failed: int = 0
    error: str = ""
    duration_ms: float = 0.0

    @property
    def total(self) -> int:
        return self.passed + self.failed

    @property
    def score(self) -> float:
        if self.total == 0:
            return 0.0
        return self.passed / self.total


@dataclass
class HumanEvalReport:
    """Aggregate report for HumanEval evaluation."""

    total_problems: int = 0
    results: list[CodeGenResult] = field(default_factory=list)

    @property
    def pass_at_1(self) -> float:
        """Fraction of problems where all test cases passed."""
        if self.total_problems == 0:
            return 0.0
        passed_all = sum(1 for r in self.results if r.failed == 0 and r.error == "")
        return passed_all / self.total_problems

    @property
    def avg_score(self) -> float:
        if not self.results:
            return 0.0
        return sum(r.score for r in self.results) / len(self.results)

    def summary(self) -> str:
        return (
            f"HumanEval: {self.total_problems} problems | "
            f"pass@1: {self.pass_at_1:.1%} | "
            f"avg score: {self.avg_score:.1%}"
        )


class HumanEvalEvaluator:
    """Evaluate code generation against HumanEval-style test cases."""

    # Type of test runner to use
    runner: Literal["subprocess", "exec"] = "subprocess"

    def load_dataset(self, path: str | Path) -> list[ProblemSample]:
        """Load problems from a JSONL dataset."""
        samples: list[ProblemSample] = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                data = json.loads(line)
                samples.append(ProblemSample(
                    trace_id=data["trace_id"],
                    question=data["question"],
                    expected_answer=data["expected_answer"],
                    language=data.get("language", "python"),
                    test_cases=data.get("test_cases", []),
                ))
        return samples

    def evaluate(
        self,
        candidate_code: str,
        test_cases: list[str],
        *,
        language: str = "python",
        timeout: int = 30,
    ) -> CodeGenResult:
        """Run test cases against a candidate solution and return results.

        Uses subprocess to execute in an isolated Python process.
        ``candidate_code`` should define the function(s) without asserts.
        """
        if language != "python":
            return CodeGenResult(trace_id="", error=f"unsupported language: {language}")

        if not test_cases:
            return CodeGenResult(trace_id="")

        passed = 0
        failed = 0
        error = ""
        start = time.perf_counter()

        for tc in test_cases:
            script = f"{candidate_code}\n\n{tc}"
            try:
                if self.runner == "subprocess":
                    result = subprocess.run(
                        [sys.executable, "-c", script],
                        capture_output=True, text=True,
                        timeout=timeout,
                    )
                    if result.returncode == 0:
                        passed += 1
                    else:
                        failed += 1
                        if not error:
                            error = result.stderr.strip() or result.stdout.strip()
                else:
                    # fallback: exec in-process (for testing/docstring-only)
                    namespace: dict = {}
                    exec(candidate_code, namespace)
                    exec(tc, namespace)
                    passed += 1
            except subprocess.TimeoutExpired:
                failed += 1
                if not error:
                    error = f"timeout after {timeout}s"
            except Exception as e:
                failed += 1
                if not error:
                    error = str(e)

        elapsed = (time.perf_counter() - start) * 1000
        return CodeGenResult(
            trace_id="",
            passed=passed,
            failed=failed,
            error=error[:500],
            duration_ms=round(elapsed, 1),
        )

    def evaluate_all(
        self,
        dataset_path: str | Path,
        solutions: dict[str, str],
    ) -> HumanEvalReport:
        """Evaluate all solutions in a dict ``{trace_id: code}`` against the dataset."""
        samples = self.load_dataset(dataset_path)
        results: list[CodeGenResult] = []

        for sample in samples:
            code = solutions.get(sample.trace_id, "")
            if not code:
                results.append(CodeGenResult(
                    trace_id=sample.trace_id, error="no solution provided",
                ))
                continue
            result = self.evaluate(code, sample.test_cases, language=sample.language)
            result.trace_id = sample.trace_id
            results.append(result)

        report = HumanEvalReport(total_problems=len(results), results=results)
        return report
