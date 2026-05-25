"""Tests for agentnexus.evaluation.humaneval — HumanEval evaluator."""

import json
import subprocess

import pytest

from agentnexus.evaluation.humaneval import (
    CodeGenResult,
    HumanEvalEvaluator,
    HumanEvalReport,
    ProblemSample,
)


class TestProblemSample:
    def test_creation(self):
        s = ProblemSample(
            trace_id="t1", question="write fib",
            expected_answer="def fib(n): ...", language="python",
            test_cases=["assert fib(1)==1"],
        )
        assert s.trace_id == "t1"
        assert s.language == "python"


class TestCodeGenResult:
    def test_defaults(self):
        r = CodeGenResult(trace_id="t1")
        assert r.total == 0
        assert r.score == 0.0

    def test_all_passed(self):
        r = CodeGenResult(trace_id="t1", passed=3, failed=0)
        assert r.score == 1.0
        assert r.total == 3

    def test_partial(self):
        r = CodeGenResult(trace_id="t1", passed=2, failed=1)
        assert r.score == pytest.approx(2 / 3)

    def test_all_failed(self):
        r = CodeGenResult(trace_id="t1", passed=0, failed=3)
        assert r.score == 0.0

    def test_error_no_total(self):
        r = CodeGenResult(trace_id="t1", error="syntax error")
        assert r.score == 0.0


class TestHumanEvalReport:
    def test_defaults(self):
        report = HumanEvalReport()
        assert report.pass_at_1 == 0.0
        assert report.avg_score == 0.0

    def test_pass_at_1_all_pass(self):
        report = HumanEvalReport(total_problems=2, results=[
            CodeGenResult(trace_id="a", passed=2, failed=0),
            CodeGenResult(trace_id="b", passed=1, failed=0),
        ])
        assert report.pass_at_1 == 1.0

    def test_pass_at_1_mixed(self):
        report = HumanEvalReport(total_problems=2, results=[
            CodeGenResult(trace_id="a", passed=2, failed=0),
            CodeGenResult(trace_id="b", passed=0, failed=1),
        ])
        assert report.pass_at_1 == 0.5

    def test_pass_at_1_error_not_counted(self):
        report = HumanEvalReport(total_problems=2, results=[
            CodeGenResult(trace_id="a", passed=2, failed=0),
            CodeGenResult(trace_id="b", error="timeout"),
        ])
        assert report.pass_at_1 == 0.5

    def test_avg_score(self):
        report = HumanEvalReport(total_problems=2, results=[
            CodeGenResult(trace_id="a", passed=2, failed=0),
            CodeGenResult(trace_id="b", passed=1, failed=1),
        ])
        assert report.avg_score == pytest.approx(0.75)

    def test_summary(self):
        report = HumanEvalReport(total_problems=5)
        s = report.summary()
        assert "5 problems" in s
        assert "pass@1" in s


class TestHumanEvalEvaluator:
    def test_load_dataset(self, tmp_path):
        fp = tmp_path / "dataset.jsonl"
        fp.write_text(json.dumps({
            "trace_id": "t1", "question": "q", "expected_answer": "a",
            "language": "python", "test_cases": ["assert 1==1"],
        }) + "\n", encoding="utf-8")

        evaluator = HumanEvalEvaluator()
        samples = evaluator.load_dataset(str(fp))
        assert len(samples) == 1
        assert samples[0].trace_id == "t1"
        assert samples[0].test_cases == ["assert 1==1"]

    def test_load_dataset_skip_empty(self, tmp_path):
        fp = tmp_path / "dataset.jsonl"
        fp.write_text("\n\n", encoding="utf-8")
        evaluator = HumanEvalEvaluator()
        assert evaluator.load_dataset(str(fp)) == []

    def test_evaluate_all_passed(self, mocker, tmp_path):
        fp = tmp_path / "humaneval.jsonl"
        fp.write_text(json.dumps({
            "trace_id": "t1", "question": "q", "expected_answer": "a",
            "language": "python", "test_cases": ["assert 1==1"],
        }) + "\n", encoding="utf-8")

        mock_subprocess = mocker.patch("subprocess.run")
        mock_subprocess.return_value.returncode = 0

        evaluator = HumanEvalEvaluator()
        report = evaluator.evaluate_all(str(fp), {"t1": "def foo(): pass"})
        assert report.total_problems == 1
        assert report.pass_at_1 == 1.0

    def test_evaluate_all_failed(self, mocker, tmp_path):
        fp = tmp_path / "humaneval.jsonl"
        fp.write_text(json.dumps({
            "trace_id": "t1", "question": "q", "expected_answer": "a",
            "language": "python", "test_cases": ["assert 1==2"],
        }) + "\n", encoding="utf-8")

        mock_subprocess = mocker.patch("subprocess.run")
        mock_subprocess.return_value.returncode = 1
        mock_subprocess.return_value.stderr = "AssertionError"

        evaluator = HumanEvalEvaluator()
        report = evaluator.evaluate_all(str(fp), {"t1": "def foo(): pass"})
        assert report.total_problems == 1
        assert report.pass_at_1 == 0.0

    def test_evaluate_all_missing_solution(self, tmp_path):
        fp = tmp_path / "humaneval.jsonl"
        fp.write_text(json.dumps({
            "trace_id": "t1", "question": "q", "expected_answer": "a",
            "language": "python", "test_cases": ["assert 1==1"],
        }) + "\n", encoding="utf-8")

        evaluator = HumanEvalEvaluator()
        report = evaluator.evaluate_all(str(fp), {})
        assert report.total_problems == 1
        assert report.pass_at_1 == 0.0
        assert "no solution" in report.results[0].error

    def test_evaluate_with_real_code(self):
        evaluator = HumanEvalEvaluator()
        evaluator.runner = "exec"
        code = "def add(a, b): return a + b"
        result = evaluator.evaluate(code, ["assert add(1, 2) == 3"])
        assert result.passed == 1
        assert result.failed == 0

    def test_evaluate_with_real_code_failure(self):
        evaluator = HumanEvalEvaluator()
        evaluator.runner = "exec"
        code = "def add(a, b): return a - b"
        result = evaluator.evaluate(code, ["assert add(1, 2) == 3"])
        assert result.passed == 0
        assert result.failed == 1

    def test_evaluate_unsupported_language(self):
        evaluator = HumanEvalEvaluator()
        result = evaluator.evaluate("code", [], language="javascript")
        assert "unsupported" in result.error

    def test_evaluate_timeout(self, mocker):
        mock_run = mocker.patch("subprocess.run")
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="python", timeout=30)

        evaluator = HumanEvalEvaluator()
        result = evaluator.evaluate("pass", ["assert 1==1"])
        assert result.failed == 1
        assert "timeout" in result.error

    def test_evaluate_exception(self, mocker):
        mock_run = mocker.patch("subprocess.run")
        mock_run.side_effect = RuntimeError("boom")

        evaluator = HumanEvalEvaluator()
        result = evaluator.evaluate("pass", ["assert 1==1"])
        assert result.failed == 1
        assert "boom" in result.error

    def test_evaluate_no_test_cases(self):
        evaluator = HumanEvalEvaluator()
        result = evaluator.evaluate("pass", [])
        assert result.total == 0

    def test_evaluate_all_multiple_problems(self, mocker, tmp_path):
        fp = tmp_path / "humaneval.jsonl"
        fp.write_text(
            json.dumps({"trace_id": "t1", "question": "q", "expected_answer": "a",
                        "language": "python", "test_cases": ["assert 1==1"]}) + "\n" +
            json.dumps({"trace_id": "t2", "question": "q2", "expected_answer": "a2",
                        "language": "python", "test_cases": ["assert 2==2", "assert 3==3"]}) + "\n",
            encoding="utf-8",
        )

        mock_run = mocker.patch("subprocess.run")
        mock_run.return_value.returncode = 0

        evaluator = HumanEvalEvaluator()
        report = evaluator.evaluate_all(str(fp), {"t1": "pass", "t2": "pass"})
        assert report.total_problems == 2
        assert report.pass_at_1 == 1.0
