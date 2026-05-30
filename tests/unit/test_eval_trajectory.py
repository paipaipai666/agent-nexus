"""Tests for agentnexus.evaluation.trajectory — dataclasses and deterministic checks."""


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
        assert "issue_0" in s
        assert "issue_4" in s
        assert "issue_5" not in s


class TestCheckDuplicateCalls:
    def _make_tool_span(self, tool_name, params=""):
        return {"name": "tool", "input": {"tool_name": tool_name, "params": params}}

    def test_no_duplicates(self):
        spans = [
            self._make_tool_span("web_search", "a"),
            self._make_tool_span("python_execute", "b"),
            self._make_tool_span("memory_search", "c"),
        ]
        report = TrajectoryReport(trace_id="t", total_spans=3)
        TrajectoryEvaluator()._check_duplicate_calls(spans, report)
        assert report.score == 10.0
        assert len(report.issues) == 0

    def test_two_identical_not_enough(self):
        spans = [
            self._make_tool_span("web_search", "query1"),
            self._make_tool_span("web_search", "query1"),
        ]
        report = TrajectoryReport(trace_id="t", total_spans=2)
        TrajectoryEvaluator()._check_duplicate_calls(spans, report)
        assert report.score == 10.0

    def test_three_identical(self):
        spans = [
            self._make_tool_span("web_search", "query1"),
            self._make_tool_span("web_search", "query1"),
            self._make_tool_span("web_search", "query1"),
        ]
        report = TrajectoryReport(trace_id="t", total_spans=3)
        TrajectoryEvaluator()._check_duplicate_calls(spans, report)
        assert report.score == 8.5
        assert len(report.issues) == 1
        assert report.issues[0].check == "duplicate_calls"
        assert report.issues[0].severity == "error"

    def test_different_params_same_tool_no_flag(self):
        spans = [
            self._make_tool_span("web_search", "query1"),
            self._make_tool_span("web_search", "query2"),
            self._make_tool_span("web_search", "query3"),
        ]
        report = TrajectoryReport(trace_id="t", total_spans=3)
        TrajectoryEvaluator()._check_duplicate_calls(spans, report)
        assert report.score == 10.0

    def test_stops_after_first_detection(self):
        spans = [
            self._make_tool_span("web_search", "q"),
            self._make_tool_span("web_search", "q"),
            self._make_tool_span("web_search", "q"),
            self._make_tool_span("web_search", "q"),
            self._make_tool_span("web_search", "q"),
        ]
        report = TrajectoryReport(trace_id="t", total_spans=5)
        TrajectoryEvaluator()._check_duplicate_calls(spans, report)
        assert report.score == 8.5
        assert len(report.issues) == 1


class TestCheckRepeatedFailures:
    def _make_tool_span(self, tool_name, status="ok"):
        return {"name": "tool", "input": {"tool_name": tool_name},
                "metadata": {"status": status}}

    def test_no_failures(self):
        spans = [self._make_tool_span("web_search", "ok")] * 5
        report = TrajectoryReport(trace_id="t", total_spans=5)
        TrajectoryEvaluator()._check_repeated_failures(spans, report)
        assert report.score == 10.0

    def test_two_failures_no_flag(self):
        spans = [
            self._make_tool_span("web_search", "error"),
            self._make_tool_span("web_search", "error"),
            self._make_tool_span("web_search", "ok"),
        ]
        report = TrajectoryReport(trace_id="t", total_spans=3)
        TrajectoryEvaluator()._check_repeated_failures(spans, report)
        assert report.score == 10.0

    def test_three_consecutive_failures(self):
        spans = [
            self._make_tool_span("python_execute", "error"),
            self._make_tool_span("python_execute", "error"),
            self._make_tool_span("python_execute", "error"),
        ]
        report = TrajectoryReport(trace_id="t", total_spans=3)
        TrajectoryEvaluator()._check_repeated_failures(spans, report)
        assert report.score == 8.5
        assert report.issues[0].check == "repeated_failures"

    def test_failure_streak_broken_by_success(self):
        spans = [
            self._make_tool_span("web_search", "error"),
            self._make_tool_span("web_search", "error"),
            self._make_tool_span("web_search", "ok"),
            self._make_tool_span("web_search", "error"),
            self._make_tool_span("web_search", "error"),
            self._make_tool_span("web_search", "error"),
        ]
        report = TrajectoryReport(trace_id="t", total_spans=6)
        TrajectoryEvaluator()._check_repeated_failures(spans, report)
        assert report.score == 8.5  # only the second streak of 3 triggers


class TestCheckLoops:
    def test_no_loop(self):
        spans = []
        report = TrajectoryReport(trace_id="t", total_spans=0)
        TrajectoryEvaluator()._check_loops(spans, report)
        assert report.score == 10.0

    def test_seven_llm_calls_no_loop(self):
        spans = [{"name": "llm"}] * 7
        report = TrajectoryReport(trace_id="t", total_spans=7)
        TrajectoryEvaluator()._check_loops(spans, report)
        assert report.score == 10.0

    def test_loop_detected(self):
        spans = [{"name": "llm"}] * 8
        report = TrajectoryReport(trace_id="t", total_spans=8)
        TrajectoryEvaluator()._check_loops(spans, report)
        assert report.score == 8.0
        assert len(report.issues) == 1
        assert report.issues[0].check == "loop_detection"
        assert report.issues[0].severity == "error"

    def test_many_llm_calls(self):
        spans = [{"name": "llm"}] * 15
        report = TrajectoryReport(trace_id="t", total_spans=15)
        TrajectoryEvaluator()._check_loops(spans, report)
        assert report.score == 8.0
        assert len(report.issues) == 1


class TestCheckErrorCascade:
    def test_no_errors(self):
        spans = [{"name": "llm", "metadata": {"status": "ok"}}] * 5
        report = TrajectoryReport(trace_id="t", total_spans=5)
        TrajectoryEvaluator()._check_error_cascade(spans, report)
        assert report.score == 10.0

    def test_two_consecutive_errors_no_flag(self):
        spans = [
            {"name": "llm", "metadata": {"status": "error"}},
            {"name": "llm", "metadata": {"status": "error"}},
            {"name": "llm", "metadata": {"status": "ok"}},
        ]
        report = TrajectoryReport(trace_id="t", total_spans=3)
        TrajectoryEvaluator()._check_error_cascade(spans, report)
        assert report.score == 10.0

    def test_three_consecutive_errors(self):
        spans = [
            {"name": "llm", "metadata": {"status": "error"}},
            {"name": "llm", "metadata": {"status": "error"}},
            {"name": "llm", "metadata": {"status": "error"}},
        ]
        report = TrajectoryReport(trace_id="t", total_spans=3)
        TrajectoryEvaluator()._check_error_cascade(spans, report)
        assert report.score == 8.5
        assert report.issues[0].check == "error_cascade"

    def test_error_streak_broken(self):
        spans = [
            {"name": "llm", "metadata": {"status": "error"}},
            {"name": "llm", "metadata": {"status": "error"}},
            {"name": "llm", "metadata": {"status": "ok"}},
            {"name": "llm", "metadata": {"status": "error"}},
            {"name": "llm", "metadata": {"status": "error"}},
            {"name": "llm", "metadata": {"status": "error"}},
        ]
        report = TrajectoryReport(trace_id="t", total_spans=6)
        TrajectoryEvaluator()._check_error_cascade(spans, report)
        assert report.score == 8.5


class TestCheckStepEfficiency:
    def test_good_ratio(self):
        llm = [{"name": "llm"}] * 3
        tools = [{"name": "tool"}] * 3
        report = TrajectoryReport(trace_id="t", total_spans=6)
        TrajectoryEvaluator()._check_step_efficiency(llm, tools, report)
        assert report.score == 10.0

    def test_low_ratio(self):
        llm = [{"name": "llm"}] * 10
        tools = [{"name": "tool"}] * 2
        report = TrajectoryReport(trace_id="t", total_spans=12)
        TrajectoryEvaluator()._check_step_efficiency(llm, tools, report)
        assert report.score == 9.0
        assert report.issues[0].check == "step_efficiency"

    def test_no_tools_no_penalty(self):
        llm = [{"name": "llm"}] * 10
        tools = []
        report = TrajectoryReport(trace_id="t", total_spans=10)
        TrajectoryEvaluator()._check_step_efficiency(llm, tools, report)
        assert report.score == 10.0


class TestEvaluateOne:
    def test_basic_evaluation(self):
        spans = [
            {"name": "task", "start_time": 0, "trace_id": "t"},
            {"name": "llm", "start_time": 1, "trace_id": "t", "metadata": {"status": "ok"}},
        ]
        report = TrajectoryEvaluator()._evaluate_one("test", spans)
        assert report.trace_id == "test"
        assert report.total_spans == 2
        assert report.score == 10.0

    def test_score_floored_at_zero(self):
        spans = [{"name": "llm", "start_time": i, "metadata": {"status": "error"}}
                 for i in range(10)]
        report = TrajectoryEvaluator()._evaluate_one("test", spans)
        # loop: -2.0, error_cascade: -1.5, step_efficiency: 0 (no tools)
        assert report.score >= 0

    def test_spans_sorted_by_start_time(self):
        spans = [
            {"name": "llm", "start_time": 5, "metadata": {"status": "ok"}},
            {"name": "task", "start_time": 0},
            {"name": "llm", "start_time": 3, "metadata": {"status": "ok"}},
        ]
        report = TrajectoryEvaluator()._evaluate_one("test", spans)
        assert report.total_spans == 3


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
