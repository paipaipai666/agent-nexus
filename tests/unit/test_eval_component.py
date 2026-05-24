"""Tests for agentnexus.evaluation.component — ComponentEvaluator."""

import json

from agentnexus.evaluation.component import (
    ComponentEvaluator,
    ComponentIssue,
    ComponentReport,
)


class TestComponentIssue:
    def test_create(self):
        issue = ComponentIssue(agent="coder", severity="error", detail="test detail")
        assert issue.agent == "coder"
        assert issue.severity == "error"
        assert issue.detail == "test detail"
        assert issue.trace_id == ""

    def test_with_trace_id(self):
        issue = ComponentIssue(agent="executor", severity="warning",
                               detail="fail", trace_id="trace-1")
        assert issue.trace_id == "trace-1"


class TestComponentReport:
    def test_defaults(self):
        report = ComponentReport()
        assert report.total_traces == 0
        assert report.issues == []
        assert report.by_agent == {}
        assert report.by_tool == {}
        assert report.issue_count == 0
        assert report.passed is True  # no agents means all pass

    def test_passed_all_scores_above_threshold(self):
        report = ComponentReport(
            by_agent={"coder": {"score": 8.0, "count": 1},
                      "executor": {"score": 7.0, "count": 2}}
        )
        assert report.passed is True

    def test_passed_one_low_score(self):
        report = ComponentReport(
            by_agent={"coder": {"score": 5.0, "count": 1},
                      "executor": {"score": 9.0, "count": 2}}
        )
        assert report.passed is False

    def test_passed_edge_score(self):
        report = ComponentReport(
            by_agent={"coder": {"score": 6.0, "count": 1}}
        )
        assert report.passed is True

    def test_issue_count(self):
        report = ComponentReport()
        report.issues.append(ComponentIssue("coder", "error", "fail"))
        report.issues.append(ComponentIssue("executor", "warning", "warn"))
        assert report.issue_count == 2


class TestComponentEvaluatorCheckCoder:
    def test_no_code_spans(self):
        evaluator = ComponentEvaluator()
        report = ComponentReport()
        stats: dict[str, list[float]] = {"coder": [], "researcher": [],
                                         "executor": [], "analyst": []}
        evaluator._check_coder([], "tid", report, stats)
        assert stats["coder"] == []  # no spans → no score appended
        assert report.issue_count == 0

    def test_schema_violation(self):
        evaluator = ComponentEvaluator()
        report = ComponentReport()
        stats: dict[str, list[float]] = {"coder": [], "researcher": [],
                                         "executor": [], "analyst": []}
        spans = [{"name": "code_node", "output": "SCHEMA_VIOLATION occurred",
                  "metadata": {}}]
        evaluator._check_coder(spans, "tid", report, stats)
        assert stats["coder"][0] == 8.0  # 10 - 2
        assert report.issue_count == 1
        assert report.issues[0].severity == "error"

    def test_truncated_output(self):
        evaluator = ComponentEvaluator()
        report = ComponentReport()
        stats: dict[str, list[float]] = {"coder": [], "researcher": [],
                                         "executor": [], "analyst": []}
        spans = [{"name": "code_node", "output": "some code",
                  "metadata": {"status": "truncated"}}]
        evaluator._check_coder(spans, "tid", report, stats)
        assert stats["coder"][0] == 9.0  # 10 - 1
        assert report.issues[0].severity == "warning"

    def test_missing_main_with_code_result(self):
        evaluator = ComponentEvaluator()
        report = ComponentReport()
        stats: dict[str, list[float]] = {"coder": [], "researcher": [],
                                         "executor": [], "analyst": []}
        spans = [{"name": "code_node", "output": "code_result something",
                  "metadata": {}}]
        evaluator._check_coder(spans, "tid", report, stats)
        assert stats["coder"][0] == 9.5  # 10 - 0.5

    def test_main_present_no_penalty(self):
        evaluator = ComponentEvaluator()
        report = ComponentReport()
        stats: dict[str, list[float]] = {"coder": [], "researcher": [],
                                         "executor": [], "analyst": []}
        spans = [{"name": "code_node", "output": "if __name__ == '__main__':\n    pass",
                  "metadata": {}}]
        evaluator._check_coder(spans, "tid", report, stats)
        assert stats["coder"][0] == 10.0


class TestComponentEvaluatorCheckResearcher:
    def test_no_research_spans(self):
        evaluator = ComponentEvaluator()
        report = ComponentReport()
        stats: dict[str, list[float]] = {"coder": [], "researcher": [],
                                         "executor": [], "analyst": []}
        evaluator._check_researcher([], "tid", report, stats)
        assert stats["researcher"] == []

    def test_missing_source_citations(self):
        evaluator = ComponentEvaluator()
        report = ComponentReport()
        stats: dict[str, list[float]] = {"coder": [], "researcher": [],
                                         "executor": [], "analyst": []}
        spans = [{"name": "research_node", "output": "some random text without references"}]
        evaluator._check_researcher(spans, "tid", report, stats)
        assert stats["researcher"][0] == 8.0  # 10 - 2
        assert report.issue_count == 1

    def test_with_source_citations(self):
        evaluator = ComponentEvaluator()
        report = ComponentReport()
        stats: dict[str, list[float]] = {"coder": [], "researcher": [],
                                         "executor": [], "analyst": []}
        spans = [{"name": "research_node", "output": "Research shows X SourceClaim: Y"}]
        evaluator._check_researcher(spans, "tid", report, stats)
        assert stats["researcher"][0] == 10.0

    def test_empty_output(self):
        evaluator = ComponentEvaluator()
        report = ComponentReport()
        stats: dict[str, list[float]] = {"coder": [], "researcher": [],
                                         "executor": [], "analyst": []}
        spans = [{"name": "research_node", "output": ""}]
        evaluator._check_researcher(spans, "tid", report, stats)
        # Empty output triggers both "no source" and "empty result"
        assert stats["researcher"][0] == 5.0  # 10 - 2 - 3
        assert report.issue_count == 2


class TestComponentEvaluatorCheckExecutor:
    def test_no_exec_spans(self):
        evaluator = ComponentEvaluator()
        report = ComponentReport()
        stats: dict[str, list[float]] = {"coder": [], "researcher": [],
                                         "executor": [], "analyst": []}
        evaluator._check_executor([], "tid", report, stats)
        assert stats["executor"] == []

    def test_successful_execution(self):
        evaluator = ComponentEvaluator()
        report = ComponentReport()
        stats: dict[str, list[float]] = {"coder": [], "researcher": [],
                                         "executor": [], "analyst": []}
        spans = [{"name": "execute_node", "input": "web_search",
                  "output": "results", "metadata": {"status": "ok"}}]
        evaluator._check_executor(spans, "tid", report, stats)
        assert stats["executor"][0] == 10.0
        assert report.by_tool["web_search"]["total"] == 1
        assert report.by_tool["web_search"]["success"] == 1

    def test_exception_in_output(self):
        evaluator = ComponentEvaluator()
        report = ComponentReport()
        stats: dict[str, list[float]] = {"coder": [], "researcher": [],
                                         "executor": [], "analyst": []}
        spans = [{"name": "execute_node", "input": "python_execute",
                  "output": "Exception: division by zero",
                  "metadata": {"status": "error"}}]
        evaluator._check_executor(spans, "tid", report, stats)
        # -1 for exception text + -2 for >50% failure rate (1/1 failed)
        assert stats["executor"][0] == 7.0
        assert report.by_tool["python_execute"]["total"] == 1
        assert report.by_tool["python_execute"]["success"] == 0

    def test_high_failure_rate(self):
        evaluator = ComponentEvaluator()
        report = ComponentReport()
        stats: dict[str, list[float]] = {"coder": [], "researcher": [],
                                         "executor": [], "analyst": []}
        spans = [
            {"name": "execute_node", "input": "code_executor",
             "output": "Exception: fail", "metadata": {"status": "error"}},
            {"name": "execute_node", "input": "code_executor",
             "output": "Exception: fail2", "metadata": {"status": "error"}},
        ]
        evaluator._check_executor(spans, "tid", report, stats)
        # -1 for each exception (2) + -2 for >50% failure rate = 10 - 2 - 2 = 6
        assert stats["executor"][0] == 6.0
        assert report.issue_count == 1

    def test_tool_categorization_fallback(self):
        evaluator = ComponentEvaluator()
        report = ComponentReport()
        stats: dict[str, list[float]] = {"coder": [], "researcher": [],
                                         "executor": [], "analyst": []}
        spans = [{"name": "execute_node", "input": "custom_tool",
                  "output": "ok", "metadata": {"status": "ok"}}]
        evaluator._check_executor(spans, "tid", report, stats)
        assert stats["executor"][0] == 10.0
        assert "unknown" in report.by_tool


class TestComponentEvaluatorCheckAnalyst:
    def test_no_analyst_spans(self):
        evaluator = ComponentEvaluator()
        report = ComponentReport()
        stats: dict[str, list[float]] = {"coder": [], "researcher": [],
                                         "executor": [], "analyst": []}
        evaluator._check_analyst([], "tid", report, stats)
        assert stats["analyst"] == []

    def test_fallback_activated(self):
        evaluator = ComponentEvaluator()
        report = ComponentReport()
        stats: dict[str, list[float]] = {"coder": [], "researcher": [],
                                         "executor": [], "analyst": []}
        spans = [{"name": "analyst_node", "output": "fallback strategy applied"}]
        evaluator._check_analyst(spans, "tid", report, stats)
        assert stats["analyst"][0] == 9.0  # 10 - 1
        assert report.issue_count == 1
        assert report.issues[0].severity == "warning"

    def test_invalid_critic_score(self):
        evaluator = ComponentEvaluator()
        report = ComponentReport()
        stats: dict[str, list[float]] = {"coder": [], "researcher": [],
                                         "executor": [], "analyst": []}
        spans = [{"name": "analyst_node",
                  "output": "{'critique_score': 15.0}"}]
        evaluator._check_analyst(spans, "tid", report, stats)
        assert stats["analyst"][0] == 9.0  # 10 - 1
        assert report.issue_count == 1
        assert "Invalid critic score" in report.issues[0].detail

    def test_valid_critic_score(self):
        evaluator = ComponentEvaluator()
        report = ComponentReport()
        stats: dict[str, list[float]] = {"coder": [], "researcher": [],
                                         "executor": [], "analyst": []}
        spans = [{"name": "analyst_node",
                  "output": "{'critique_score': 7.5}"}]
        evaluator._check_analyst(spans, "tid", report, stats)
        assert stats["analyst"][0] == 10.0  # no deduction


class TestComponentEvaluatorEvaluateAll:
    def test_empty_directory(self, tmp_path):
        evaluator = ComponentEvaluator()
        report = evaluator.evaluate_all(str(tmp_path))
        assert report.total_traces == 0
        assert report.by_agent == {}

    def test_with_trace_file(self, tmp_path):
        trace_file = tmp_path / "trace.jsonl"
        lines = [
            {"trace_id": "t1", "name": "code_node", "output": "OK",
             "metadata": {}},
            {"trace_id": "t1", "name": "research_node", "output": "SourceClaim: X",
             "metadata": {}},
            {"trace_id": "t1", "name": "execute_node", "input": "web_search",
             "output": "results", "metadata": {"status": "ok"}},
        ]
        trace_file.write_text("\n".join(json.dumps(line) for line in lines) + "\n",
                              encoding="utf-8")

        evaluator = ComponentEvaluator()
        report = evaluator.evaluate_all(str(tmp_path))
        assert report.total_traces == 1
        assert "coder" in report.by_agent
        assert "researcher" in report.by_agent
        assert "executor" in report.by_agent
        assert report.by_tool["web_search"]["total"] == 1

    def test_skip_bad_json_lines(self, tmp_path):
        trace_file = tmp_path / "bad.jsonl"
        trace_file.write_text("not json\n{\"trace_id\": \"t1\", \"name\": \"code_node\"}\n",
                              encoding="utf-8")
        evaluator = ComponentEvaluator()
        report = evaluator.evaluate_all(str(tmp_path))
        assert report.total_traces == 1

    def test_analyst_not_required(self, tmp_path):
        """Analyst has no spans, but other agents do, so no error."""
        trace_file = tmp_path / "trace.jsonl"
        lines = [
            {"trace_id": "t1", "name": "execute_node", "input": "web_search",
             "output": "ok", "metadata": {"status": "ok"}},
        ]
        trace_file.write_text("\n".join(json.dumps(line) for line in lines) + "\n",
                              encoding="utf-8")
        evaluator = ComponentEvaluator()
        report = evaluator.evaluate_all(str(tmp_path))
        assert "executor" in report.by_agent
        assert "analyst" not in report.by_agent
