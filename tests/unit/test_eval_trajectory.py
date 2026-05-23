"""Tests for agentnexus.evaluation.trajectory — dataclasses and deterministic checks."""

import pytest

from agentnexus.evaluation.trajectory import (
    TrajectoryEvaluator,
    TrajectoryIssue,
    TrajectoryReport,
)


class TestTrajectoryIssue:
    def test_creation(self):
        issue = TrajectoryIssue(check="loop_detection", severity="error", detail="loop found")
        assert issue.check == "loop_detection"
        assert issue.severity == "error"
        assert issue.detail == "loop found"
        assert issue.evidence == ""

    def test_with_evidence(self):
        issue = TrajectoryIssue(
            check="duplicate_calls", severity="error",
            detail="duplicate", evidence="count=3",
        )
        assert issue.evidence == "count=3"


class TestTrajectoryReport:
    def test_defaults(self):
        report = TrajectoryReport(trace_id="test", total_spans=5)
        assert report.score == 10.0
        assert report.issues == []
        assert report.passed

    def test_failed_low_score(self):
        report = TrajectoryReport(trace_id="test", total_spans=5, score=4.0)
        assert not report.passed

    def test_passed_at_threshold(self):
        report = TrajectoryReport(trace_id="test", total_spans=5, score=6.0)
        assert report.passed

    def test_failed_below_threshold(self):
        report = TrajectoryReport(trace_id="test", total_spans=5, score=5.9)
        assert not report.passed

    def test_issue_count(self):
        report = TrajectoryReport(trace_id="test", total_spans=5)
        report.issues.append(TrajectoryIssue(check="test", severity="error", detail="detail"))
        assert report.issue_count == 1

    def test_issue_count_zero(self):
        report = TrajectoryReport(trace_id="test", total_spans=5)
        assert report.issue_count == 0

    def test_summary(self):
        report = TrajectoryReport(trace_id="tid", total_spans=3, score=8.0)
        s = report.summary()
        assert "tid" in s
        assert "8" in s
        assert "3" in s
        assert "Issues: 0" in s

    def test_summary_with_issues(self):
        report = TrajectoryReport(trace_id="tid", total_spans=3, score=7.0)
        report.issues.append(TrajectoryIssue(
            check="duplicate_calls", severity="error", detail="tool called 3x",
        ))
        s = report.summary()
        assert "[ERROR]" in s
        assert "duplicate_calls" in s
        assert "tool called 3x" in s

    def test_summary_shows_only_first_five_issues(self):
        report = TrajectoryReport(trace_id="tid", total_spans=5, score=5.0)
        for i in range(10):
            report.issues.append(TrajectoryIssue(
                check=f"issue_{i}", severity="warning", detail="",
            ))
        s = report.summary()
        # Only first 5 issues appear
        assert "issue_0" in s
        assert "issue_4" in s
        assert "issue_5" not in s


class TestCheckDuplicateCalls:
    def test_no_duplicates(self):
        spans = [
            {"name": "search", "input": "a"},
            {"name": "read", "input": "b"},
            {"name": "code", "input": "c"},
        ]
        report = TrajectoryReport(trace_id="t", total_spans=3)
        TrajectoryEvaluator()._check_duplicate_calls(spans, report)
        assert report.score == 10.0
        assert len(report.issues) == 0

    def test_two_identical_not_enough(self):
        spans = [
            {"name": "search", "input": "query1"},
            {"name": "search", "input": "query1"},
        ]
        report = TrajectoryReport(trace_id="t", total_spans=2)
        TrajectoryEvaluator()._check_duplicate_calls(spans, report)
        assert report.score == 10.0

    def test_three_identical(self):
        spans = [
            {"name": "search", "input": "query1"},
            {"name": "search", "input": "query1"},
            {"name": "search", "input": "query1"},
        ]
        report = TrajectoryReport(trace_id="t", total_spans=3)
        TrajectoryEvaluator()._check_duplicate_calls(spans, report)
        assert report.score == 8.5
        assert len(report.issues) == 1
        assert report.issues[0].check == "duplicate_calls"
        assert report.issues[0].severity == "error"

    def test_different_input_same_name_no_flag(self):
        spans = [
            {"name": "search", "input": "query1"},
            {"name": "search", "input": "query2"},
            {"name": "search", "input": "query3"},
        ]
        report = TrajectoryReport(trace_id="t", total_spans=3)
        TrajectoryEvaluator()._check_duplicate_calls(spans, report)
        assert report.score == 10.0

    def test_same_input_different_names_no_flag(self):
        spans = [
            {"name": "search", "input": "same"},
            {"name": "read", "input": "same"},
            {"name": "code", "input": "same"},
        ]
        report = TrajectoryReport(trace_id="t", total_spans=3)
        TrajectoryEvaluator()._check_duplicate_calls(spans, report)
        assert report.score == 10.0

    def test_stops_after_first_detection(self):
        spans = [
            {"name": "search", "input": "q"},
            {"name": "search", "input": "q"},
            {"name": "search", "input": "q"},
            {"name": "search", "input": "q"},
            {"name": "search", "input": "q"},
        ]
        report = TrajectoryReport(trace_id="t", total_spans=5)
        TrajectoryEvaluator()._check_duplicate_calls(spans, report)
        assert report.score == 8.5
        assert len(report.issues) == 1


class TestCheckToolAppropriateness:
    def test_code_error_with_research(self):
        spans = [
            {"name": "code_execute", "metadata": {"status": "error"}},
            {"name": "research_docs", "metadata": {}},
        ]
        report = TrajectoryReport(trace_id="t", total_spans=2)
        TrajectoryEvaluator()._check_tool_appropriateness(spans, report)
        assert report.score == 10.0
        assert len(report.issues) == 0

    def test_code_error_without_research(self):
        spans = [{"name": "code_execute", "metadata": {"status": "error"}}]
        report = TrajectoryReport(trace_id="t", total_spans=1)
        TrajectoryEvaluator()._check_tool_appropriateness(spans, report)
        assert report.score == 9.0
        assert len(report.issues) == 1
        assert report.issues[0].check == "tool_appropriateness"
        assert report.issues[0].severity == "warning"

    def test_no_code_error_no_issue(self):
        spans = [{"name": "code_execute", "metadata": {"status": "ok"}}]
        report = TrajectoryReport(trace_id="t", total_spans=1)
        TrajectoryEvaluator()._check_tool_appropriateness(spans, report)
        assert report.score == 10.0

    def test_code_error_case_insensitive(self):
        spans = [{"name": "Code_Execute", "metadata": {"status": "error"}}]
        report = TrajectoryReport(trace_id="t", total_spans=1)
        TrajectoryEvaluator()._check_tool_appropriateness(spans, report)
        assert report.score == 9.0

    def test_research_case_insensitive(self):
        spans = [
            {"name": "code_execute", "metadata": {"status": "error"}},
            {"name": "Research_API", "metadata": {}},
        ]
        report = TrajectoryReport(trace_id="t", total_spans=2)
        TrajectoryEvaluator()._check_tool_appropriateness(spans, report)
        assert report.score == 10.0


class TestCheckLoops:
    def test_no_loop(self):
        spans = []
        report = TrajectoryReport(trace_id="t", total_spans=0)
        TrajectoryEvaluator()._check_loops(spans, report)
        assert report.score == 10.0

    def test_three_plan_nodes_no_loop(self):
        spans = [{"name": "plan_node"}] * 3
        report = TrajectoryReport(trace_id="t", total_spans=3)
        TrajectoryEvaluator()._check_loops(spans, report)
        assert report.score == 10.0

    def test_loop_detected(self):
        spans = [{"name": "plan_node"}] * 4
        report = TrajectoryReport(trace_id="t", total_spans=4)
        TrajectoryEvaluator()._check_loops(spans, report)
        assert report.score == 8.0
        assert len(report.issues) == 1
        assert report.issues[0].check == "loop_detection"
        assert report.issues[0].severity == "error"

    def test_many_plan_nodes(self):
        spans = [{"name": "plan_node"}] * 10
        report = TrajectoryReport(trace_id="t", total_spans=10)
        TrajectoryEvaluator()._check_loops(spans, report)
        assert report.score == 8.0
        assert len(report.issues) == 1

    def test_mixed_spans_with_loop(self):
        spans = [
            {"name": "task"},
            {"name": "plan_node"},
            {"name": "llm"},
            {"name": "plan_node"},
            {"name": "llm"},
            {"name": "plan_node"},
            {"name": "code_execute"},
            {"name": "plan_node"},
        ]
        report = TrajectoryReport(trace_id="t", total_spans=8)
        TrajectoryEvaluator()._check_loops(spans, report)
        assert report.score == 8.0


class TestCheckRetryEfficiency:
    def test_no_analyst_spans(self):
        spans = [{"name": "llm"}, {"name": "task"}]
        report = TrajectoryReport(trace_id="t", total_spans=2)
        TrajectoryEvaluator()._check_retry_efficiency(spans, report)
        assert report.score == 10.0

    def test_single_analyst_no_check(self):
        spans = [{"name": "analyst_node", "output": {"result": "{'critique_score': 8.5}"}}]
        report = TrajectoryReport(trace_id="t", total_spans=1)
        TrajectoryEvaluator()._check_retry_efficiency(spans, report)
        assert report.score == 10.0

    def test_improving_score_no_deduction(self):
        spans = [
            {"name": "analyst_node", "output": {"eval": "{'critique_score': 7.0}"}},
            {"name": "analyst_node", "output": {"eval": "{'critique_score': 8.5}"}},
        ]
        report = TrajectoryReport(trace_id="t", total_spans=2)
        TrajectoryEvaluator()._check_retry_efficiency(spans, report)
        assert report.score == 10.0

    def test_dropping_score_deducted(self):
        spans = [
            {"name": "analyst_node", "output": {"eval": "{'critique_score': 8.5}"}},
            {"name": "analyst_node", "output": {"eval": "{'critique_score': 7.0}"}},
        ]
        report = TrajectoryReport(trace_id="t", total_spans=2)
        TrajectoryEvaluator()._check_retry_efficiency(spans, report)
        assert report.score == 9.5
        assert len(report.issues) == 1
        assert report.issues[0].check == "retry_efficiency"

    def test_multiple_drops_each_deducted(self):
        spans = [
            {"name": "analyst_node", "output": {"eval": "{'critique_score': 9.0}"}},
            {"name": "analyst_node", "output": {"eval": "{'critique_score': 7.5}"}},
            {"name": "analyst_node", "output": {"eval": "{'critique_score': 6.0}"}},
        ]
        report = TrajectoryReport(trace_id="t", total_spans=3)
        TrajectoryEvaluator()._check_retry_efficiency(spans, report)
        assert report.score == 9.0
        assert len(report.issues) == 2

    def test_small_drop_not_deducted(self):
        spans = [
            {"name": "analyst_node", "output": {"eval": "{'critique_score': 8.5}"}},
            {"name": "analyst_node", "output": {"eval": "{'critique_score': 7.6}"}},  # drop 0.9 < 1.0
        ]
        report = TrajectoryReport(trace_id="t", total_spans=2)
        TrajectoryEvaluator()._check_retry_efficiency(spans, report)
        assert report.score == 10.0

    def test_analyst_case_insensitive(self):
        spans = [
            {"name": "Analyst_Node", "output": {"eval": "{'critique_score': 8.0}"}},
            {"name": "Analyst_Node", "output": {"eval": "{'critique_score': 6.5}"}},
        ]
        report = TrajectoryReport(trace_id="t", total_spans=2)
        TrajectoryEvaluator()._check_retry_efficiency(spans, report)
        assert report.score == 9.5


class TestCheckPlanAdherence:
    def test_no_plan_span(self):
        spans = [{"name": "llm"}, {"name": "task"}]
        report = TrajectoryReport(trace_id="t", total_spans=2)
        TrajectoryEvaluator()._check_plan_adherence(spans, report)
        assert report.score == 10.0

    def test_plan_input_without_research_or_code(self):
        spans = [{"name": "plan_node", "input": "just a plan"}]
        report = TrajectoryReport(trace_id="t", total_spans=1)
        TrajectoryEvaluator()._check_plan_adherence(spans, report)
        assert report.score == 10.0

    def test_within_bounds_no_deduction(self):
        spans = [
            {"name": "plan_node", "input": "research: step1\ncode: step2"},
            {"name": "research_node"},
            {"name": "code_node"},
        ]
        report = TrajectoryReport(trace_id="t", total_spans=3)
        TrajectoryEvaluator()._check_plan_adherence(spans, report)
        assert report.score == 10.0

    def test_exceeds_double_deducted(self):
        spans = [
            {"name": "plan_node", "input": "research: step1\ncode: step2"},
            {"name": "research_node"}, {"name": "research_node"}, {"name": "research_node"},
            {"name": "code_node"}, {"name": "code_node"}, {"name": "code_node"},
        ]
        report = TrajectoryReport(trace_id="t", total_spans=7)
        TrajectoryEvaluator()._check_plan_adherence(spans, report)
        # plan_steps = 2 (1 research: + 1 code:), actual = 6, 6 > 4 → deduct 1.0
        assert report.score == 9.0
        assert len(report.issues) == 1
        assert report.issues[0].check == "plan_adherence"

    def test_uses_first_plan_span(self):
        spans = [
            {"name": "plan_node", "input": "research: first plan"},
            {"name": "research_node"}, {"name": "research_node"}, {"name": "research_node"},
            {"name": "plan_node", "input": "code: revised plan"},  # second plan ignored
        ]
        report = TrajectoryReport(trace_id="t", total_spans=4)
        TrajectoryEvaluator()._check_plan_adherence(spans, report)
        # plan_steps = 1 (1 research:), actual = 3, 3 > 2 → deduct 1.0
        assert report.score == 9.0


class TestEvaluateOne:
    def test_basic_evaluation(self):
        spans = [
            {"name": "task", "start_time": 0, "trace_id": "t"},
            {"name": "llm", "start_time": 1, "trace_id": "t"},
        ]
        report = TrajectoryEvaluator()._evaluate_one("test", spans)
        assert report.trace_id == "test"
        assert report.total_spans == 2
        assert report.score == 10.0

    def test_score_floored_at_zero(self):
        spans = [
            {"name": "plan_node", "start_time": 0},
            {"name": "plan_node", "start_time": 1},
            {"name": "plan_node", "start_time": 2},
            {"name": "plan_node", "start_time": 3},
            {"name": "code_execute", "metadata": {"status": "error"}},
        ]
        report = TrajectoryEvaluator()._evaluate_one("test", spans)
        # loop: -2.0, tool_appropriateness: -1.0 → score = 7.0
        assert report.score == 7.0

    def test_spans_sorted_by_start_time(self):
        spans = [
            {"name": "llm", "start_time": 5},
            {"name": "task", "start_time": 0},
            {"name": "llm", "start_time": 3},
        ]
        report = TrajectoryEvaluator()._evaluate_one("test", spans)
        assert report.total_spans == 3


class TestLoadTraceFromFile:
    def test_matching_trace_returns_spans(self, tmp_path):
        f = tmp_path / "traces.jsonl"
        f.write_text(
            '{"trace_id": "abc", "name": "task"}\n'
            '{"trace_id": "abc", "name": "llm"}\n'
        )
        spans = TrajectoryEvaluator._load_trace_from_file(str(f), "abc")
        assert spans is not None
        assert len(spans) == 2

    def test_no_matching_trace_returns_none(self, tmp_path):
        f = tmp_path / "traces.jsonl"
        f.write_text('{"trace_id": "other", "name": "task"}')
        spans = TrajectoryEvaluator._load_trace_from_file(str(f), "notfound")
        assert spans is None

    def test_skips_invalid_json(self, tmp_path):
        f = tmp_path / "traces.jsonl"
        f.write_text(
            '{"trace_id": "abc", "name": "task"}\n'
            'broken\n'
            '{"trace_id": "abc", "name": "llm"}\n'
        )
        spans = TrajectoryEvaluator._load_trace_from_file(str(f), "abc")
        assert spans is not None
        assert len(spans) == 2

    def test_file_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            TrajectoryEvaluator._load_trace_from_file(
                str(tmp_path / "nope.jsonl"), "abc"
            )


class TestEvaluateFile:
    def test_empty_file(self, tmp_path):
        f = tmp_path / "empty.jsonl"
        f.write_text("")
        reports = TrajectoryEvaluator().evaluate_file(str(f))
        assert reports == []

    def test_single_trace(self, tmp_path):
        f = tmp_path / "single.jsonl"
        f.write_text('{"trace_id": "t1", "name": "task", "start_time": 0}\n')
        reports = TrajectoryEvaluator().evaluate_file(str(f))
        assert len(reports) == 1
        assert reports[0].trace_id == "t1"

    def test_multiple_traces(self, tmp_path):
        f = tmp_path / "multi.jsonl"
        f.write_text(
            '{"trace_id": "a", "name": "task", "start_time": 0}\n'
            '{"trace_id": "b", "name": "task", "start_time": 0}\n'
            '{"trace_id": "a", "name": "llm", "start_time": 1}\n'
        )
        reports = TrajectoryEvaluator().evaluate_file(str(f))
        assert len(reports) == 2
        ids = {r.trace_id for r in reports}
        assert ids == {"a", "b"}

    def test_skips_invalid_json_lines(self, tmp_path):
        f = tmp_path / "mixed.jsonl"
        f.write_text(
            '{"trace_id": "a", "name": "task"}\n'
            'not json\n'
        )
        reports = TrajectoryEvaluator().evaluate_file(str(f))
        assert len(reports) == 1


class TestEvaluateTrace:
    def test_trace_not_found(self, tmp_path):
        d = tmp_path / "traces"
        d.mkdir()
        result = TrajectoryEvaluator().evaluate_trace("missing", str(d))
        assert result is None

    def test_trace_found(self, tmp_path):
        d = tmp_path / "traces"
        d.mkdir()
        (d / "log.jsonl").write_text(
            '{"trace_id": "t1", "name": "task", "start_time": 0}\n'
            '{"trace_id": "t1", "name": "llm", "start_time": 1}\n'
        )
        result = TrajectoryEvaluator().evaluate_trace("t1", str(d))
        assert result is not None
        assert result.trace_id == "t1"

    def test_searches_reverse_order(self, tmp_path):
        d = tmp_path / "traces"
        d.mkdir()
        (d / "a.jsonl").write_text(
            '{"trace_id": "t1", "name": "task", "input": {"task": "from_a"}, "start_time": 0}\n'
        )
        (d / "b.jsonl").write_text(
            '{"trace_id": "t1", "name": "task", "input": {"task": "from_b"}, "start_time": 0}\n'
        )
        result = TrajectoryEvaluator().evaluate_trace("t1", str(d))
        # Should match from later file (b.jsonl sorted reverse)
        assert result is not None


class TestEvaluateAll:
    def test_empty_directory(self, tmp_path):
        d = tmp_path / "traces"
        d.mkdir()
        reports = TrajectoryEvaluator().evaluate_all(str(d))
        assert reports == []

    def test_multiple_files(self, tmp_path):
        d = tmp_path / "traces"
        d.mkdir()
        (d / "f1.jsonl").write_text(
            '{"trace_id": "a", "name": "task", "start_time": 0}\n'
        )
        (d / "f2.jsonl").write_text(
            '{"trace_id": "b", "name": "task", "start_time": 0}\n'
        )
        reports = TrajectoryEvaluator().evaluate_all(str(d))
        assert len(reports) == 2
        ids = {r.trace_id for r in reports}
        assert ids == {"a", "b"}

    def test_same_trace_across_files(self, tmp_path):
        d = tmp_path / "traces"
        d.mkdir()
        (d / "f1.jsonl").write_text(
            '{"trace_id": "a", "name": "task", "start_time": 0}\n'
        )
        (d / "f2.jsonl").write_text(
            '{"trace_id": "a", "name": "llm", "start_time": 1}\n'
        )
        reports = TrajectoryEvaluator().evaluate_all(str(d))
        assert len(reports) == 1
        assert reports[0].total_spans == 2
