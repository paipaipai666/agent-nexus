"""Tests for agentnexus.evaluation.swebench — SWE-bench style evaluator."""

import json

from agentnexus.evaluation.swebench import SWEBenchEvaluator, SWEBenchReport


class TestSWEBenchReport:
    def test_defaults(self):
        report = SWEBenchReport()
        assert report.resolve_rate == 0.0
        assert report.unresolved == []

    def test_all_resolved(self):
        report = SWEBenchReport(total_problems=3, resolved=3)
        assert report.resolve_rate == 1.0

    def test_partial_resolved(self):
        report = SWEBenchReport(total_problems=4, resolved=3)
        assert report.resolve_rate == 0.75

    def test_summary(self):
        report = SWEBenchReport(total_problems=2, resolved=1, unresolved=["swe_002"])
        s = report.summary()
        assert "2 issues" in s
        assert "50.0%" in s
        assert "unresolved: 1" in s


class TestSWEBenchEvaluator:
    def test_evaluate_all_resolved(self, mocker, tmp_path):
        fp = tmp_path / "swebench.jsonl"
        fp.write_text(json.dumps({
            "trace_id": "swe_001", "question": "fix bug",
            "expected_answer": "fixed code",
            "language": "python", "test_cases": ["assert 1==1"],
            "repo": "test-repo", "issue_id": 1,
        }) + "\n", encoding="utf-8")

        mock_run = mocker.patch("subprocess.run")
        mock_run.return_value.returncode = 0

        evaluator = SWEBenchEvaluator()
        report = evaluator.evaluate_all(str(fp), {"swe_001": "def fix(): pass"})
        assert report.total_problems == 1
        assert report.resolve_rate == 1.0
        assert report.unresolved == []

    def test_evaluate_all_unresolved(self, mocker, tmp_path):
        fp = tmp_path / "swebench.jsonl"
        fp.write_text(json.dumps({
            "trace_id": "swe_001", "question": "fix bug",
            "expected_answer": "fixed code",
            "language": "python", "test_cases": ["assert 1==2"],
            "repo": "test-repo", "issue_id": 1,
        }) + "\n", encoding="utf-8")

        mock_run = mocker.patch("subprocess.run")
        mock_run.return_value.returncode = 1
        mock_run.return_value.stderr = "fail"

        evaluator = SWEBenchEvaluator()
        report = evaluator.evaluate_all(str(fp), {"swe_001": "wrong fix"})
        assert report.total_problems == 1
        assert report.resolve_rate == 0.0
        assert "swe_001" in report.unresolved

    def test_evaluate_all_mixed(self, mocker, tmp_path):
        fp = tmp_path / "swebench.jsonl"
        fp.write_text(
            json.dumps({"trace_id": "swe_001", "question": "fix 1",
                        "expected_answer": "a", "language": "python",
                        "test_cases": ["assert 1==1"], "repo": "r", "issue_id": 1}) + "\n" +
            json.dumps({"trace_id": "swe_002", "question": "fix 2",
                        "expected_answer": "b", "language": "python",
                        "test_cases": ["assert 1==2"], "repo": "r", "issue_id": 2}) + "\n",
            encoding="utf-8",
        )

        mock_run = mocker.patch("subprocess.run")
        mock_run.return_value.returncode = 0

        def side_effect(*args, **kwargs):
            import subprocess
            cmd = args[0]
            code = cmd[-1] if isinstance(cmd, list) else ""
            if "assert 1==2" in code:
                raise subprocess.CalledProcessError(1, cmd)
            return mocker.MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = side_effect

        evaluator = SWEBenchEvaluator()
        report = evaluator.evaluate_all(str(fp), {"swe_001": "ok", "swe_002": "bad"})
        assert report.total_problems == 2
        assert report.resolve_rate == 0.5
