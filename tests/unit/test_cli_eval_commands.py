"""Tests for CLI eval commands: agent, trajectory, ci, component,
hallucination, tool-selection, coherence, and run (all-fail branch)."""

import json
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
        patch("agentnexus.cli.eval_cmd.get_settings", return_value=mock),
        patch("agentnexus.core.config.get_settings", return_value=mock),
    ):
        yield mock


# ── eval agent ──────────────────────────────────────────────────


class TestEvalAgent:
    def test_no_data(self, mock_settings):
        with patch("agentnexus.evaluation.agent_eval.AgentEvaluator") as mock_cls:
            mock_report = MagicMock()
            mock_report.total_traces = 0
            mock_cls.return_value.evaluate_all.return_value = mock_report

            result = runner.invoke(app, ["eval", "agent"])
            assert result.exit_code == 0
            assert "暂无可评估的 trace 数据" in result.output

    def test_with_data_no_tools(self, mock_settings):
        with patch("agentnexus.evaluation.agent_eval.AgentEvaluator") as mock_cls:
            mock_report = MagicMock()
            mock_report.total_traces = 3
            mock_report.summary.return_value = "Summary text"
            mock_report.tool_breakdown = {}
            mock_report.failed_traces = []
            mock_report.passed = True
            mock_cls.return_value.evaluate_all.return_value = mock_report

            result = runner.invoke(app, ["eval", "agent"])
            assert "Summary text" in result.output
            assert "无工具调用记录" in result.output

    def test_with_data_and_tools(self, mock_settings):
        with patch("agentnexus.evaluation.agent_eval.AgentEvaluator") as mock_cls:
            mock_report = MagicMock()
            mock_report.total_traces = 3
            mock_report.summary.return_value = "Summary"
            mock_report.tool_breakdown = {
                "search": {"calls": 10, "errors": 1, "success_rate": 0.9},
                "code": {"calls": 5, "errors": 0, "success_rate": 1.0},
            }
            mock_report.failed_traces = []
            mock_report.passed = True
            mock_cls.return_value.evaluate_all.return_value = mock_report

            result = runner.invoke(app, ["eval", "agent"])
            assert "工具调用明细" in result.output
            assert "search" in result.output
            assert "code" in result.output

    def test_with_failed_traces(self, mock_settings):
        with patch("agentnexus.evaluation.agent_eval.AgentEvaluator") as mock_cls:
            mock_failed = MagicMock()
            mock_failed.trace_id = "trace-001"
            mock_failed.had_error = True
            mock_failed.had_truncation = False
            mock_failed.had_answer = True
            mock_failed.task_preview = "Some task"

            mock_report = MagicMock()
            mock_report.total_traces = 3
            mock_report.summary.return_value = "Summary"
            mock_report.tool_breakdown = {}
            mock_report.failed_traces = [mock_failed]
            mock_report.passed = False
            mock_cls.return_value.evaluate_all.return_value = mock_report

            result = runner.invoke(app, ["eval", "agent"])
            assert "异常 Trace" in result.output
            assert "Some task" in result.output
            assert "LLM 错误" in result.output
            assert "部分指标未达阈值" in result.output


# ── eval trajectory ─────────────────────────────────────────────


class TestEvalTrajectory:
    def test_single_trace_not_found(self, mock_settings):
        with patch(
            "agentnexus.evaluation.trajectory.TrajectoryEvaluator"
        ) as mock_cls:
            mock_cls.return_value.evaluate_trace.return_value = None

            result = runner.invoke(
                app, ["eval", "trajectory", "--trace-id", "nonexistent"]
            )
            assert "未找到 Trace" in result.output

    def test_single_trace(self, mock_settings):
        with patch(
            "agentnexus.evaluation.trajectory.TrajectoryEvaluator"
        ) as mock_cls:
            mock_report = MagicMock()
            mock_report.trace_id = "trace-001"
            mock_report.total_spans = 5
            mock_report.score = 8.5
            mock_report.issue_count = 0
            mock_report.passed = True
            mock_report.issues = []
            mock_cls.return_value.evaluate_trace.return_value = mock_report

            result = runner.invoke(
                app, ["eval", "trajectory", "--trace-id", "trace-001"]
            )
            assert "trace-001" in result.output
            assert "PASS" in result.output

    def test_all_traces_empty(self, mock_settings):
        with patch(
            "agentnexus.evaluation.trajectory.TrajectoryEvaluator"
        ) as mock_cls:
            mock_cls.return_value.evaluate_all.return_value = []

            result = runner.invoke(app, ["eval", "trajectory"])
            assert "暂无 trace" in result.output

    def test_all_traces_with_data(self, mock_settings):
        with patch(
            "agentnexus.evaluation.trajectory.TrajectoryEvaluator"
        ) as mock_cls:
            mock_report1 = MagicMock()
            mock_report1.trace_id = "trace-001"
            mock_report1.total_spans = 5
            mock_report1.score = 9.0
            mock_report1.issue_count = 0
            mock_report1.passed = True
            mock_report1.issues = []

            mock_issue = MagicMock()
            mock_issue.check = "check1"
            mock_issue.detail = "some detail"
            mock_report2 = MagicMock()
            mock_report2.trace_id = "trace-002"
            mock_report2.total_spans = 3
            mock_report2.score = 4.0
            mock_report2.issue_count = 2
            mock_report2.passed = False
            mock_report2.issues = [mock_issue]

            mock_cls.return_value.evaluate_all.return_value = [
                mock_report1,
                mock_report2,
            ]

            result = runner.invoke(app, ["eval", "trajectory"])
            assert "通过: 1/2" in result.output
            assert "FAIL trace-002" in result.output
            assert "check1" in result.output


# ── eval ci ─────────────────────────────────────────────────────


class TestEvalCi:
    def test_no_data(self, mock_settings):
        with patch("agentnexus.evaluation.agent_eval.AgentEvaluator") as mock_cls:
            mock_report = MagicMock()
            mock_report.total_traces = 0
            mock_cls.return_value.evaluate_all.return_value = mock_report

            result = runner.invoke(app, ["eval", "ci"])
            assert "No traces to evaluate" in result.output
            assert result.exit_code == 0

    def test_passed(self, mock_settings):
        with patch("agentnexus.evaluation.agent_eval.AgentEvaluator") as mock_cls:
            mock_report = MagicMock()
            mock_report.total_traces = 2
            mock_report.summary.return_value = "Summary"
            mock_report.failed_traces = []
            mock_report.passed = True
            mock_cls.return_value.evaluate_all.return_value = mock_report

            result = runner.invoke(app, ["eval", "ci"])
            assert result.exit_code == 0

    def test_failed(self, mock_settings):
        with patch("agentnexus.evaluation.agent_eval.AgentEvaluator") as mock_cls:
            mock_report = MagicMock()
            mock_report.total_traces = 2
            mock_report.summary.return_value = "Summary"
            mock_report.failed_traces = [MagicMock()]
            mock_report.passed = False
            mock_cls.return_value.evaluate_all.return_value = mock_report

            result = runner.invoke(app, ["eval", "ci"])
            assert result.exit_code == 1
            assert "traces 异常" in result.output


# ── eval component ──────────────────────────────────────────────


class TestEvalComponent:
    def test_no_data(self, mock_settings):
        with patch(
            "agentnexus.evaluation.component.ComponentEvaluator"
        ) as mock_cls:
            mock_report = MagicMock()
            mock_report.total_traces = 0
            mock_cls.return_value.evaluate_all.return_value = mock_report

            result = runner.invoke(app, ["eval", "component"])
            assert "暂无 trace" in result.output

    def test_with_data(self, mock_settings):
        with patch(
            "agentnexus.evaluation.component.ComponentEvaluator"
        ) as mock_cls:
            mock_report = MagicMock()
            mock_report.total_traces = 5
            mock_report.issue_count = 2
            mock_report.by_agent = {
                "Coder": {"score": 8.5, "count": 10},
                "Researcher": {"score": 7.0, "count": 5},
            }
            mock_report.by_tool = {
                "search": {"success": 8, "total": 10},
                "code": {"success": 5, "total": 5},
            }
            mock_report.issues = [
                MagicMock(severity="high", agent="Coder", detail="some issue"),
            ]
            mock_cls.return_value.evaluate_all.return_value = mock_report

            result = runner.invoke(app, ["eval", "component"])
            assert "Coder" in result.output
            assert "8.5" in result.output
            assert "80.0%" in result.output
            assert "[HIGH]" in result.output


# ── eval hallucination ──────────────────────────────────────────


class TestEvalHallucination:
    def test_single_not_found(self, mock_settings):
        with patch(
            "agentnexus.evaluation.hallucination.HallucinationDetector"
        ) as mock_cls:
            mock_cls.return_value.evaluate_trace.return_value = None

            result = runner.invoke(
                app, ["eval", "hallucination", "--trace-id", "nonexistent"]
            )
            assert "未找到 Trace" in result.output

    def test_single_trace(self, mock_settings):
        with patch(
            "agentnexus.evaluation.hallucination.HallucinationDetector"
        ) as mock_cls:
            mock_report = MagicMock()
            mock_report.trace_id = "trace-001"
            mock_report.total_claims = 10
            mock_report.unsupported_claims = 1
            mock_report.hallucination_rate = 0.1
            mock_report.passed = True
            mock_report.flagged_claims = []
            mock_cls.return_value.evaluate_trace.return_value = mock_report

            result = runner.invoke(
                app, ["eval", "hallucination", "--trace-id", "trace-001"]
            )
            assert "trace-001" in result.output
            assert "PASS" in result.output

    def test_all_empty(self, mock_settings):
        with patch(
            "agentnexus.evaluation.hallucination.HallucinationDetector"
        ) as mock_cls:
            mock_cls.return_value.evaluate_all.return_value = []

            result = runner.invoke(app, ["eval", "hallucination"])
            assert "暂无评估数据" in result.output

    def test_all_with_failed(self, mock_settings):
        with patch(
            "agentnexus.evaluation.hallucination.HallucinationDetector"
        ) as mock_cls:
            mock_report1 = MagicMock()
            mock_report1.trace_id = "trace-001"
            mock_report1.total_claims = 10
            mock_report1.unsupported_claims = 0
            mock_report1.hallucination_rate = 0.0
            mock_report1.passed = True
            mock_report1.flagged_claims = []

            mock_report2 = MagicMock()
            mock_report2.trace_id = "trace-002"
            mock_report2.total_claims = 5
            mock_report2.unsupported_claims = 3
            mock_report2.hallucination_rate = 0.6
            mock_report2.passed = False
            mock_report2.flagged_claims = ["claim1", "claim2"]

            mock_cls.return_value.evaluate_all.return_value = [
                mock_report1,
                mock_report2,
            ]

            result = runner.invoke(app, ["eval", "hallucination"])
            assert "整体幻觉率" in result.output
            assert "FAIL trace-002" in result.output
            assert "claim1" in result.output


# ── eval tool-selection ─────────────────────────────────────────


class TestEvalToolSelection:
    def test_no_data(self, mock_settings):
        with patch(
            "agentnexus.evaluation.tool_selection.ToolSelectionEvaluator"
        ) as mock_cls:
            mock_report = MagicMock()
            mock_report.total_queries = 0
            mock_cls.return_value.evaluate_from_traces.return_value = mock_report

            result = runner.invoke(app, ["eval", "tool-selection"])
            assert "暂无评估数据" in result.output

    def test_with_data(self, mock_settings):
        with patch(
            "agentnexus.evaluation.tool_selection.ToolSelectionEvaluator"
        ) as mock_cls:
            mock_report = MagicMock()
            mock_report.total_queries = 20
            mock_report.passed = True
            mock_report.accuracy = 0.85
            mock_report.correct = 17
            mock_report.by_tool = {
                "search": {"correct": 10, "total": 12},
                "code": {"correct": 7, "total": 8},
            }
            mock_report.mismatches = [
                {
                    "expected": "search",
                    "actual": "code",
                    "query": "find something",
                },
            ]
            mock_cls.return_value.evaluate_from_traces.return_value = mock_report

            result = runner.invoke(app, ["eval", "tool-selection"])
            assert "85.0%" in result.output
            assert "PASS" in result.output
            assert "不匹配" in result.output


# ── eval coherence ──────────────────────────────────────────────


class TestEvalCoherence:
    def test_single_not_found(self, mock_settings):
        with patch(
            "agentnexus.evaluation.coherence.CoherenceEvaluator"
        ) as mock_cls:
            mock_cls.return_value.evaluate_trace.return_value = None

            result = runner.invoke(
                app, ["eval", "coherence", "--trace-id", "nonexistent"]
            )
            assert "未找到 Trace" in result.output

    def test_single_trace(self, mock_settings):
        with patch(
            "agentnexus.evaluation.coherence.CoherenceEvaluator"
        ) as mock_cls:
            mock_report = MagicMock()
            mock_report.trace_id = "trace-001"
            mock_report.total_steps = 5
            mock_report.coherence_score = 9.0
            mock_report.passed = True
            mock_report.issues = ""
            mock_cls.return_value.evaluate_trace.return_value = mock_report

            result = runner.invoke(
                app, ["eval", "coherence", "--trace-id", "trace-001"]
            )
            assert "trace-001" in result.output
            assert "PASS" in result.output

    def test_all_empty(self, mock_settings):
        with patch(
            "agentnexus.evaluation.coherence.CoherenceEvaluator"
        ) as mock_cls:
            mock_cls.return_value.evaluate_all.return_value = []

            result = runner.invoke(app, ["eval", "coherence"])
            assert "暂无评估数据" in result.output

    def test_all_with_failed(self, mock_settings):
        with patch(
            "agentnexus.evaluation.coherence.CoherenceEvaluator"
        ) as mock_cls:
            mock_report1 = MagicMock()
            mock_report1.trace_id = "trace-001"
            mock_report1.total_steps = 5
            mock_report1.coherence_score = 9.0
            mock_report1.passed = True
            mock_report1.issues = ""

            mock_report2 = MagicMock()
            mock_report2.trace_id = "trace-002"
            mock_report2.total_steps = 3
            mock_report2.coherence_score = 4.0
            mock_report2.passed = False
            mock_report2.issues = "coherence issue details"

            mock_cls.return_value.evaluate_all.return_value = [
                mock_report1,
                mock_report2,
            ]

            result = runner.invoke(app, ["eval", "coherence"])
            assert "通过: 1/2" in result.output
            assert "FAIL trace-002" in result.output
            assert "coherence issue details" in result.output


# ── eval run (all combos fail branch) ────────────────────────────


class TestEvalRun:
    def test_all_combos_fail(self):
        with patch("agentnexus.cli.eval_cmd.RAGEvaluator") as mock_cls:
            mock_instance = mock_cls.return_value
            mock_instance.run_combination.side_effect = ValueError("test failure")

            result = runner.invoke(app, ["eval", "run"])
            assert "所有评估组合均失败" in result.output
            assert result.exit_code == 0

    def test_external_file_backed_dataset(self, temp_agentnexus_home):
        doc = temp_agentnexus_home / "guide.md"
        doc.write_text("# Guide\n\nBody text\n", encoding="utf-8")
        dataset = temp_agentnexus_home / "eval.jsonl"
        rows = [
            {"dataset_version": "files-v1"},
            {"knowledge_base": [{"path": "guide.md"}]},
            {"question": "Q1", "ground_truth": "A1", "reference_contexts": ["Guide"]},
        ]
        dataset.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows), encoding="utf-8")

        with patch("agentnexus.cli.eval_cmd.RAGEvaluator") as mock_cls:
            mock_instance = mock_cls.return_value
            mock_instance.run_combination.side_effect = ValueError("test failure")

            result = runner.invoke(app, ["eval", "run", "--dataset", str(dataset)])

            assert result.exit_code == 0
            assert "已加载外部数据集" in result.output
            assert "文件型" in result.output

    def test_invalid_dataset_surfaces_error(self, temp_agentnexus_home):
        dataset = temp_agentnexus_home / "bad.jsonl"
        dataset.write_text(json.dumps({"knowledge_base": ["doc one"]}, ensure_ascii=False), encoding="utf-8")

        result = runner.invoke(app, ["eval", "run", "--dataset", str(dataset)])

        assert result.exit_code != 0
