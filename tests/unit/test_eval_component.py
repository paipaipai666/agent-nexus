"""Tests for agentnexus.evaluation.component — ComponentEvaluator."""

import json

from agentnexus.evaluation.component import (
    ComponentEvaluator,
    ComponentIssue,
    ComponentReport,
)


class TestComponentIssue:
    def test_create(self):
        issue = ComponentIssue(component="llm", severity="error", detail="test detail")
        assert issue.component == "llm"
        assert issue.severity == "error"
        assert issue.detail == "test detail"
        assert issue.trace_id == ""

    def test_with_trace_id(self):
        issue = ComponentIssue(component="tool_execution", severity="warning",
                               detail="fail", trace_id="trace-1")
        assert issue.trace_id == "trace-1"


class TestComponentReport:
    def test_defaults(self):
        report = ComponentReport()
        assert report.total_traces == 0
        assert report.issues == []
        assert report.by_component == {}
        assert report.by_tool == {}
        assert report.issue_count == 0
        assert report.passed is True

    def test_passed_all_scores_above_threshold(self):
        report = ComponentReport(
            by_component={"llm": {"score": 8.0, "count": 1},
                          "tool_execution": {"score": 7.0, "count": 2}}
        )
        assert report.passed is True

    def test_passed_one_low_score(self):
        report = ComponentReport(
            by_component={"llm": {"score": 5.0, "count": 1},
                          "tool_execution": {"score": 9.0, "count": 2}}
        )
        assert report.passed is False

    def test_passed_edge_score(self):
        report = ComponentReport(
            by_component={"llm": {"score": 6.0, "count": 1}}
        )
        assert report.passed is True

    def test_issue_count(self):
        report = ComponentReport()
        report.issues.append(ComponentIssue("llm", "error", "fail"))
        report.issues.append(ComponentIssue("tool_execution", "warning", "warn"))
        assert report.issue_count == 2


class TestComponentEvaluatorCheckLlm:
    def test_no_llm_spans(self):
        evaluator = ComponentEvaluator()
        report = ComponentReport()
        stats: dict[str, list[float]] = {"llm": [], "tool_execution": [], "answer": []}
        evaluator._check_llm([], "tid", report, stats)
        assert stats["llm"] == []

    def test_llm_error(self):
        evaluator = ComponentEvaluator()
        report = ComponentReport()
        stats: dict[str, list[float]] = {"llm": [], "tool_execution": [], "answer": []}
        spans = [{"name": "llm", "metadata": {"status": "error"}}]
        evaluator._check_llm(spans, "tid", report, stats)
        # 10 - 2 (per error) - 2 (high error rate: 1/1 = 100%) = 6
        assert stats["llm"][0] == 6.0
        assert report.issue_count == 2

    def test_truncated_output(self):
        evaluator = ComponentEvaluator()
        report = ComponentReport()
        stats: dict[str, list[float]] = {"llm": [], "tool_execution": [], "answer": []}
        spans = [{"name": "llm", "metadata": {"status": "ok", "truncated": True}}]
        evaluator._check_llm(spans, "tid", report, stats)
        assert stats["llm"][0] == 9.0  # 10 - 1
        assert report.issues[0].severity == "warning"

    def test_high_error_rate(self):
        evaluator = ComponentEvaluator()
        report = ComponentReport()
        stats: dict[str, list[float]] = {"llm": [], "tool_execution": [], "answer": []}
        spans = [
            {"name": "llm", "metadata": {"status": "error"}},
            {"name": "llm", "metadata": {"status": "error"}},
            {"name": "llm", "metadata": {"status": "ok"}},
        ]
        evaluator._check_llm(spans, "tid", report, stats)
        # 2 errors - 2*2 = -4, plus -2 for high rate = 10 - 6 = 4
        assert stats["llm"][0] == 4.0

    def test_successful_llm_calls(self):
        evaluator = ComponentEvaluator()
        report = ComponentReport()
        stats: dict[str, list[float]] = {"llm": [], "tool_execution": [], "answer": []}
        spans = [{"name": "llm", "metadata": {"status": "ok"}}]
        evaluator._check_llm(spans, "tid", report, stats)
        assert stats["llm"][0] == 10.0


class TestComponentEvaluatorCheckToolExecution:
    def test_no_tool_spans(self):
        evaluator = ComponentEvaluator()
        report = ComponentReport()
        stats: dict[str, list[float]] = {"llm": [], "tool_execution": [], "answer": []}
        evaluator._check_tool_execution([], "tid", report, stats)
        assert stats["tool_execution"] == []

    def test_successful_execution(self):
        evaluator = ComponentEvaluator()
        report = ComponentReport()
        stats: dict[str, list[float]] = {"llm": [], "tool_execution": [], "answer": []}
        spans = [{"name": "tool", "input": {"tool_name": "web_search"},
                  "output": {"result_summary": "results"}, "metadata": {"status": "ok"}}]
        evaluator._check_tool_execution(spans, "tid", report, stats)
        assert stats["tool_execution"][0] == 10.0
        assert report.by_tool["web_search"]["total"] == 1
        assert report.by_tool["web_search"]["success"] == 1

    def test_tool_error(self):
        evaluator = ComponentEvaluator()
        report = ComponentReport()
        stats: dict[str, list[float]] = {"llm": [], "tool_execution": [], "answer": []}
        spans = [{"name": "tool", "input": {"tool_name": "python_execute"},
                  "output": {"result_summary": "error"}, "metadata": {"status": "error"}}]
        evaluator._check_tool_execution(spans, "tid", report, stats)
        assert report.by_tool["python_execute"]["total"] == 1
        assert report.by_tool["python_execute"]["success"] == 0

    def test_high_failure_rate(self):
        evaluator = ComponentEvaluator()
        report = ComponentReport()
        stats: dict[str, list[float]] = {"llm": [], "tool_execution": [], "answer": []}
        spans = [
            {"name": "tool", "input": {"tool_name": "code_executor"},
             "output": {}, "metadata": {"status": "error"}},
            {"name": "tool", "input": {"tool_name": "code_executor"},
             "output": {}, "metadata": {"status": "error"}},
        ]
        evaluator._check_tool_execution(spans, "tid", report, stats)
        assert stats["tool_execution"][0] == 8.0  # 10 - 2 for >50% failure
        assert report.issue_count == 1

    def test_timeout_detected(self):
        evaluator = ComponentEvaluator()
        report = ComponentReport()
        stats: dict[str, list[float]] = {"llm": [], "tool_execution": [], "answer": []}
        spans = [{"name": "tool", "input": {"tool_name": "web_search"},
                  "output": {"result_summary": "Tool timed out after 30s"},
                  "metadata": {"status": "ok"}}]
        evaluator._check_tool_execution(spans, "tid", report, stats)
        assert stats["tool_execution"][0] == 9.0  # 10 - 1 for timeout


class TestComponentEvaluatorCheckAnswer:
    def test_no_answer_with_llm_spans(self):
        evaluator = ComponentEvaluator()
        report = ComponentReport()
        stats: dict[str, list[float]] = {"llm": [], "tool_execution": [], "answer": []}
        spans = [{"name": "llm", "metadata": {"status": "ok"}}]
        evaluator._check_answer(spans, "tid", report, stats)
        assert stats["answer"][0] == 7.0  # 10 - 3 for missing answer

    def test_answer_present(self):
        evaluator = ComponentEvaluator()
        report = ComponentReport()
        stats: dict[str, list[float]] = {"llm": [], "tool_execution": [], "answer": []}
        spans = [
            {"name": "llm", "metadata": {"status": "ok"}},
            {"name": "final_answer", "output": {"answer": "The result is 42."},
             "metadata": {"status": "ok"}},
        ]
        evaluator._check_answer(spans, "tid", report, stats)
        assert stats["answer"][0] == 10.0

    def test_degraded_answer(self):
        evaluator = ComponentEvaluator()
        report = ComponentReport()
        stats: dict[str, list[float]] = {"llm": [], "tool_execution": [], "answer": []}
        spans = [{"name": "final_answer", "output": {"answer": "降级 fallback answer"},
                  "metadata": {"status": "ok"}}]
        evaluator._check_answer(spans, "tid", report, stats)
        assert stats["answer"][0] == 9.0  # 10 - 1
        assert report.issues[0].severity == "warning"

    def test_no_llm_no_answer_no_penalty(self):
        evaluator = ComponentEvaluator()
        report = ComponentReport()
        stats: dict[str, list[float]] = {"llm": [], "tool_execution": [], "answer": []}
        evaluator._check_answer([], "tid", report, stats)
        # No LLM spans and no answer spans → no score appended (nothing to evaluate)
        assert stats["answer"] == []


class TestComponentEvaluatorEvaluateAll:
    def test_empty_directory(self, tmp_path):
        evaluator = ComponentEvaluator()
        report = evaluator.evaluate_all(str(tmp_path))
        assert report.total_traces == 0
        assert report.by_component == {}

    def test_with_trace_file(self, tmp_path):
        trace_file = tmp_path / "trace.jsonl"
        lines = [
            {"trace_id": "t1", "name": "llm", "metadata": {"status": "ok"}},
            {"trace_id": "t1", "name": "tool", "input": {"tool_name": "web_search"},
             "output": {"result_summary": "ok"}, "metadata": {"status": "ok"}},
            {"trace_id": "t1", "name": "final_answer",
             "output": {"answer": "result"}, "metadata": {"status": "ok"}},
        ]
        trace_file.write_text("\n".join(json.dumps(line) for line in lines) + "\n",
                              encoding="utf-8")

        evaluator = ComponentEvaluator()
        report = evaluator.evaluate_all(str(tmp_path))
        assert report.total_traces == 1
        assert "llm" in report.by_component
        assert "tool_execution" in report.by_component
        assert "answer" in report.by_component
        assert report.by_tool["web_search"]["total"] == 1

    def test_skip_bad_json_lines(self, tmp_path):
        trace_file = tmp_path / "bad.jsonl"
        trace_file.write_text("not json\n{\"trace_id\": \"t1\", \"name\": \"llm\"}\n",
                              encoding="utf-8")
        evaluator = ComponentEvaluator()
        report = evaluator.evaluate_all(str(tmp_path))
        assert report.total_traces == 1
