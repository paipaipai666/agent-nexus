"""Tests for CLI eval trace-based commands: trajectory, ci, component,
hallucination, tool-selection, coherence."""

from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from agentnexus.cli import app

runner = CliRunner()


@pytest.fixture
def mock_settings():
    mock = MagicMock()
    mock.traces_dir = "/tmp/traces"
    with (
        patch("agentnexus.cli.eval.trace.get_settings", return_value=mock),
        patch("agentnexus.core.config.get_settings", return_value=mock),
    ):
        yield mock


def _make_trajectory_report(trace_id="t-1", total_spans=5, score=9.0,
                             issue_count=0, passed=True, issues=None):
    report = MagicMock()
    report.trace_id = trace_id
    report.total_spans = total_spans
    report.score = score
    report.issue_count = issue_count
    report.passed = passed
    report.issues = issues or []
    return report


def _make_issue(check="check1", detail="some detail"):
    issue = MagicMock()
    issue.check = check
    issue.detail = detail
    return issue


def _make_hallucination_report(trace_id="t-1", total_claims=10,
                                unsupported_claims=0, hallucination_rate=0.0,
                                passed=True, flagged_claims=None):
    report = MagicMock()
    report.trace_id = trace_id
    report.total_claims = total_claims
    report.unsupported_claims = unsupported_claims
    report.hallucination_rate = hallucination_rate
    report.passed = passed
    report.flagged_claims = flagged_claims or []
    return report


def _make_coherence_report(trace_id="t-1", total_steps=5, coherence_score=9.0,
                            passed=True, issues=""):
    report = MagicMock()
    report.trace_id = trace_id
    report.total_steps = total_steps
    report.coherence_score = coherence_score
    report.passed = passed
    report.issues = issues
    return report


# ── eval trajectory ─────────────────────────────────────────────


class TestEvalTrajectory:
    def test_single_trace_found(self, mock_settings):
        with patch(
            "agentnexus.evaluation.trajectory.TrajectoryEvaluator"
        ) as mock_cls:
            mock_cls.return_value.evaluate_trace.return_value = _make_trajectory_report()

            result = runner.invoke(
                app, ["eval", "trajectory", "--trace-id", "t-1"]
            )
            assert "t-1" in result.output
            assert "PASS" in result.output

    def test_single_trace_not_found(self, mock_settings):
        with patch(
            "agentnexus.evaluation.trajectory.TrajectoryEvaluator"
        ) as mock_cls:
            mock_cls.return_value.evaluate_trace.return_value = None

            result = runner.invoke(
                app, ["eval", "trajectory", "--trace-id", "nonexistent"]
            )
            assert "Trace not found" in result.output

    def test_all_traces_with_pass_count(self, mock_settings):
        with patch(
            "agentnexus.evaluation.trajectory.TrajectoryEvaluator"
        ) as mock_cls:
            r1 = _make_trajectory_report(trace_id="t-1", passed=True)
            r2 = _make_trajectory_report(trace_id="t-2", score=4.0,
                                          issue_count=2, passed=False,
                                          issues=[_make_issue()])
            mock_cls.return_value.evaluate_all.return_value = [r1, r2]

            result = runner.invoke(app, ["eval", "trajectory"])
            assert "Passed: 1/2" in result.output

    def test_no_data(self, mock_settings):
        with patch(
            "agentnexus.evaluation.trajectory.TrajectoryEvaluator"
        ) as mock_cls:
            mock_cls.return_value.evaluate_all.return_value = []

            result = runner.invoke(app, ["eval", "trajectory"])
            assert "No trace data" in result.output

    def test_failed_trace_shows_issues(self, mock_settings):
        with patch(
            "agentnexus.evaluation.trajectory.TrajectoryEvaluator"
        ) as mock_cls:
            issue = _make_issue(check="order_check", detail="steps out of order")
            r = _make_trajectory_report(trace_id="t-fail", score=3.0,
                                         issue_count=1, passed=False,
                                         issues=[issue])
            mock_cls.return_value.evaluate_trace.return_value = r

            result = runner.invoke(
                app, ["eval", "trajectory", "--trace-id", "t-fail"]
            )
            assert "FAIL" in result.output
            assert "order_check" in result.output
            assert "steps out of order" in result.output


# ── eval ci ─────────────────────────────────────────────────────


class TestEvalCi:
    def test_all_pass(self, mock_settings):
        with patch("agentnexus.evaluation.agent_eval.AgentEvaluator") as mock_cls:
            report = MagicMock()
            report.total_traces = 3
            report.summary.return_value = "All good"
            report.failed_traces = []
            report.passed = True
            mock_cls.return_value.evaluate_all.return_value = report

            result = runner.invoke(app, ["eval", "ci"])
            assert result.exit_code == 0

    def test_some_fail(self, mock_settings):
        with patch("agentnexus.evaluation.agent_eval.AgentEvaluator") as mock_cls:
            report = MagicMock()
            report.total_traces = 3
            report.summary.return_value = "Some bad"
            report.failed_traces = [MagicMock()]
            report.passed = False
            mock_cls.return_value.evaluate_all.return_value = report

            result = runner.invoke(app, ["eval", "ci"])
            assert result.exit_code == 1
            assert "1/3" in result.output

    def test_no_traces(self, mock_settings):
        with patch("agentnexus.evaluation.agent_eval.AgentEvaluator") as mock_cls:
            report = MagicMock()
            report.total_traces = 0
            mock_cls.return_value.evaluate_all.return_value = report

            result = runner.invoke(app, ["eval", "ci"])
            assert result.exit_code == 0
            assert "No traces" in result.output


# ── eval component ──────────────────────────────────────────────


class TestEvalComponent:
    def test_happy_path(self, mock_settings):
        with patch(
            "agentnexus.evaluation.component.ComponentEvaluator"
        ) as mock_cls:
            report = MagicMock()
            report.total_traces = 5
            report.issue_count = 1
            report.by_agent = {
                "Coder": {"score": 8.5, "count": 10},
                "Researcher": {"score": 7.0, "count": 5},
            }
            report.by_tool = {}
            report.issues = []
            mock_cls.return_value.evaluate_all.return_value = report

            result = runner.invoke(app, ["eval", "component"])
            assert "Coder" in result.output
            assert "8.5" in result.output
            assert "Total traces: 5" in result.output

    def test_with_tool_data(self, mock_settings):
        with patch(
            "agentnexus.evaluation.component.ComponentEvaluator"
        ) as mock_cls:
            report = MagicMock()
            report.total_traces = 5
            report.issue_count = 0
            report.by_agent = {"Coder": {"score": 9.0, "count": 5}}
            report.by_tool = {
                "search": {"success": 8, "total": 10},
                "code": {"success": 5, "total": 5},
            }
            report.issues = []
            mock_cls.return_value.evaluate_all.return_value = report

            result = runner.invoke(app, ["eval", "component"])
            assert "80.0%" in result.output
            assert "100.0%" in result.output

    def test_tool_with_zero_executions(self, mock_settings):
        with patch(
            "agentnexus.evaluation.component.ComponentEvaluator"
        ) as mock_cls:
            report = MagicMock()
            report.total_traces = 1
            report.issue_count = 0
            report.by_agent = {"Coder": {"score": 9.0, "count": 1}}
            report.by_tool = {
                "empty_tool": {"success": 0, "total": 0},
            }
            report.issues = []
            mock_cls.return_value.evaluate_all.return_value = report

            result = runner.invoke(app, ["eval", "component"])
            assert "0.0%" in result.output

    def test_no_data(self, mock_settings):
        with patch(
            "agentnexus.evaluation.component.ComponentEvaluator"
        ) as mock_cls:
            report = MagicMock()
            report.total_traces = 0
            mock_cls.return_value.evaluate_all.return_value = report

            result = runner.invoke(app, ["eval", "component"])
            assert "No trace data" in result.output


# ── eval hallucination ──────────────────────────────────────────


class TestEvalHallucination:
    def test_single_trace_found(self, mock_settings):
        with patch(
            "agentnexus.evaluation.hallucination.HallucinationDetector"
        ) as mock_cls:
            mock_cls.return_value.evaluate_trace.return_value = (
                _make_hallucination_report()
            )

            result = runner.invoke(
                app, ["eval", "hallucination", "--trace-id", "t-1"]
            )
            assert "t-1" in result.output
            assert "PASS" in result.output

    def test_single_trace_not_found(self, mock_settings):
        with patch(
            "agentnexus.evaluation.hallucination.HallucinationDetector"
        ) as mock_cls:
            mock_cls.return_value.evaluate_trace.return_value = None

            result = runner.invoke(
                app, ["eval", "hallucination", "--trace-id", "nonexistent"]
            )
            assert "Trace not found" in result.output

    def test_all_traces_overall_stats(self, mock_settings):
        with patch(
            "agentnexus.evaluation.hallucination.HallucinationDetector"
        ) as mock_cls:
            r1 = _make_hallucination_report(
                trace_id="t-1", total_claims=10,
                unsupported_claims=0, hallucination_rate=0.0, passed=True
            )
            r2 = _make_hallucination_report(
                trace_id="t-2", total_claims=5,
                unsupported_claims=2, hallucination_rate=0.4, passed=True
            )
            mock_cls.return_value.evaluate_all.return_value = [r1, r2]

            result = runner.invoke(app, ["eval", "hallucination"])
            assert "Overall hallucination rate" in result.output
            assert "15" in result.output  # total claims
            assert "2" in result.output   # total unsupported

    def test_zero_total_claims(self, mock_settings):
        with patch(
            "agentnexus.evaluation.hallucination.HallucinationDetector"
        ) as mock_cls:
            r = _make_hallucination_report(
                total_claims=0, unsupported_claims=0,
                hallucination_rate=0.0, passed=True
            )
            mock_cls.return_value.evaluate_all.return_value = [r]

            result = runner.invoke(app, ["eval", "hallucination"])
            assert "0.0%" in result.output

    def test_failed_trace_with_flagged_claims(self, mock_settings):
        with patch(
            "agentnexus.evaluation.hallucination.HallucinationDetector"
        ) as mock_cls:
            r = _make_hallucination_report(
                trace_id="t-bad", total_claims=5,
                unsupported_claims=3, hallucination_rate=0.6,
                passed=False, flagged_claims=["claim A", "claim B"]
            )
            mock_cls.return_value.evaluate_all.return_value = [r]

            result = runner.invoke(app, ["eval", "hallucination"])
            assert "FAIL t-bad" in result.output
            assert "claim A" in result.output

    def test_no_data(self, mock_settings):
        with patch(
            "agentnexus.evaluation.hallucination.HallucinationDetector"
        ) as mock_cls:
            mock_cls.return_value.evaluate_all.return_value = []

            result = runner.invoke(app, ["eval", "hallucination"])
            assert "No evaluation data" in result.output


# ── eval tool-selection ─────────────────────────────────────────


class TestEvalToolSelection:
    def test_happy_path(self, mock_settings):
        with patch(
            "agentnexus.evaluation.tool_selection.ToolSelectionEvaluator"
        ) as mock_cls:
            report = MagicMock()
            report.total_queries = 20
            report.passed = True
            report.accuracy = 0.85
            report.correct = 17
            report.by_tool = {
                "search": {"correct": 10, "total": 12},
                "code": {"correct": 7, "total": 8},
            }
            report.mismatches = []
            mock_cls.return_value.evaluate_from_traces.return_value = report

            result = runner.invoke(app, ["eval", "tool-selection"])
            assert "85.0%" in result.output
            assert "PASS" in result.output
            assert "17/20" in result.output

    def test_with_mismatches(self, mock_settings):
        with patch(
            "agentnexus.evaluation.tool_selection.ToolSelectionEvaluator"
        ) as mock_cls:
            report = MagicMock()
            report.total_queries = 10
            report.passed = False
            report.accuracy = 0.7
            report.correct = 7
            report.by_tool = {
                "search": {"correct": 5, "total": 6},
                "code": {"correct": 2, "total": 4},
            }
            report.mismatches = [
                {"expected": "search", "actual": "code", "query": "find docs"},
            ]
            mock_cls.return_value.evaluate_from_traces.return_value = report

            result = runner.invoke(app, ["eval", "tool-selection"])
            assert "Mismatches" in result.output
            assert "find docs" in result.output

    def test_no_mismatches(self, mock_settings):
        with patch(
            "agentnexus.evaluation.tool_selection.ToolSelectionEvaluator"
        ) as mock_cls:
            report = MagicMock()
            report.total_queries = 5
            report.passed = True
            report.accuracy = 1.0
            report.correct = 5
            report.by_tool = {"search": {"correct": 5, "total": 5}}
            report.mismatches = []
            mock_cls.return_value.evaluate_from_traces.return_value = report

            result = runner.invoke(app, ["eval", "tool-selection"])
            assert "Mismatches" not in result.output

    def test_no_data(self, mock_settings):
        with patch(
            "agentnexus.evaluation.tool_selection.ToolSelectionEvaluator"
        ) as mock_cls:
            report = MagicMock()
            report.total_queries = 0
            mock_cls.return_value.evaluate_from_traces.return_value = report

            result = runner.invoke(app, ["eval", "tool-selection"])
            assert "No evaluation data" in result.output


# ── eval coherence ──────────────────────────────────────────────


class TestEvalCoherence:
    def test_single_trace_found(self, mock_settings):
        with patch(
            "agentnexus.evaluation.coherence.CoherenceEvaluator"
        ) as mock_cls:
            mock_cls.return_value.evaluate_trace.return_value = (
                _make_coherence_report()
            )

            result = runner.invoke(
                app, ["eval", "coherence", "--trace-id", "t-1"]
            )
            assert "t-1" in result.output
            assert "PASS" in result.output

    def test_single_trace_not_found(self, mock_settings):
        with patch(
            "agentnexus.evaluation.coherence.CoherenceEvaluator"
        ) as mock_cls:
            mock_cls.return_value.evaluate_trace.return_value = None

            result = runner.invoke(
                app, ["eval", "coherence", "--trace-id", "nonexistent"]
            )
            assert "Trace not found" in result.output

    def test_all_traces_pass_count(self, mock_settings):
        with patch(
            "agentnexus.evaluation.coherence.CoherenceEvaluator"
        ) as mock_cls:
            r1 = _make_coherence_report(trace_id="t-1", passed=True)
            r2 = _make_coherence_report(
                trace_id="t-2", coherence_score=4.0, passed=False,
                issues="low coherence"
            )
            mock_cls.return_value.evaluate_all.return_value = [r1, r2]

            result = runner.invoke(app, ["eval", "coherence"])
            assert "Passed: 1/2" in result.output

    def test_no_data(self, mock_settings):
        with patch(
            "agentnexus.evaluation.coherence.CoherenceEvaluator"
        ) as mock_cls:
            mock_cls.return_value.evaluate_all.return_value = []

            result = runner.invoke(app, ["eval", "coherence"])
            assert "No evaluation data" in result.output
