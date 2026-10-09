"""Tests for agentnexus.observability.drift_detector."""

import pytest

from agentnexus.observability.drift_detector import (
    DriftDetector,
    DriftReport,
    DriftSeverity,
    DriftSignal,
    DriftSignalType,
    _keyword_overlap,
)


class TestKeywordOverlap:
    def test_identical_text_returns_one(self):
        text = "分析订单异常原因"
        score = _keyword_overlap(text, text)
        assert score == 1.0

    def test_completely_different_text_returns_zero(self):
        text_a = "分析订单异常原因"
        text_b = "hello world foo bar"
        score = _keyword_overlap(text_a, text_b)
        assert score == 0.0

    def test_partial_overlap_returns_proportion(self):
        text_a = "分析 订单 异常 原因"
        text_b = "分析 数据 异常 报告"
        score = _keyword_overlap(text_a, text_b)
        assert score == pytest.approx(2 / 6)

    def test_stop_words_are_filtered(self):
        text_a = "the cat is happy"
        text_b = "the dog is sad"
        score = _keyword_overlap(text_a, text_b)
        assert score == 0.0

    def test_empty_text_returns_zero(self):
        score = _keyword_overlap("", "hello world")
        assert score == 0.0

    def test_both_empty_returns_zero(self):
        score = _keyword_overlap("", "")
        assert score == 0.0


class TestDriftReport:
    def test_passed_true_when_no_critical_signals(self):
        report = DriftReport(
            trace_id="t1",
            signals=[
                DriftSignal(
                    signal_type=DriftSignalType.GOAL_DRIFT,
                    severity=DriftSeverity.WARNING,
                    detail="minor drift",
                ),
            ],
        )
        assert report.passed is True

    def test_passed_false_when_critical_signal_present(self):
        report = DriftReport(
            trace_id="t1",
            signals=[
                DriftSignal(
                    signal_type=DriftSignalType.GOAL_DRIFT,
                    severity=DriftSeverity.CRITICAL,
                    detail="major drift",
                ),
            ],
        )
        assert report.passed is False

    def test_warning_count(self):
        report = DriftReport(
            trace_id="t1",
            signals=[
                DriftSignal(
                    signal_type=DriftSignalType.GOAL_DRIFT,
                    severity=DriftSeverity.WARNING,
                    detail="w1",
                ),
                DriftSignal(
                    signal_type=DriftSignalType.REPEATED_STEPS,
                    severity=DriftSeverity.WARNING,
                    detail="w2",
                ),
                DriftSignal(
                    signal_type=DriftSignalType.GOAL_DRIFT,
                    severity=DriftSeverity.CRITICAL,
                    detail="c1",
                ),
            ],
        )
        assert report.warning_count == 2

    def test_critical_count(self):
        report = DriftReport(
            trace_id="t1",
            signals=[
                DriftSignal(
                    signal_type=DriftSignalType.GOAL_DRIFT,
                    severity=DriftSeverity.CRITICAL,
                    detail="c1",
                ),
                DriftSignal(
                    signal_type=DriftSignalType.SUBTASK_OVERRUN,
                    severity=DriftSeverity.CRITICAL,
                    detail="c2",
                ),
            ],
        )
        assert report.critical_count == 2

    def test_passed_true_when_no_signals(self):
        report = DriftReport(trace_id="t1")
        assert report.passed is True
        assert report.warning_count == 0
        assert report.critical_count == 0


class TestDriftDetectorGoalRelevance:
    def test_no_signal_when_goal_highly_relevant(self):
        detector = DriftDetector(original_goal="分析订单异常原因")
        signals = detector.check(step_index=1, current_goal="分析订单异常原因")
        drift_signals = [s for s in signals if s.signal_type == DriftSignalType.GOAL_DRIFT]
        assert len(drift_signals) == 0

    def test_warning_signal_when_goal_low_relevance(self):
        detector = DriftDetector(original_goal="alpha beta gamma delta epsilon")
        signals = detector.check(step_index=1, current_goal="alpha zeta eta theta iota kappa")
        drift_signals = [s for s in signals if s.signal_type == DriftSignalType.GOAL_DRIFT]
        assert len(drift_signals) == 1
        assert drift_signals[0].severity == DriftSeverity.WARNING

    def test_critical_signal_when_goal_very_low_relevance(self):
        detector = DriftDetector(original_goal="分析订单异常原因")
        signals = detector.check(step_index=1, current_goal="xyz abc def ghi")
        drift_signals = [s for s in signals if s.signal_type == DriftSignalType.GOAL_DRIFT]
        assert len(drift_signals) == 1
        assert drift_signals[0].severity == DriftSeverity.CRITICAL

    def test_no_signal_when_current_goal_empty(self):
        detector = DriftDetector(original_goal="分析订单异常原因")
        signals = detector.check(step_index=1, current_goal="")
        drift_signals = [s for s in signals if s.signal_type == DriftSignalType.GOAL_DRIFT]
        assert len(drift_signals) == 0


class TestDriftDetectorRepeatedSteps:
    def test_warning_when_consecutive_identical_tool_calls(self):
        detector = DriftDetector(original_goal="test goal", max_steps=20)
        for i in range(3):
            detector.record_step(step_index=i, tool_name="grep", params_hash="abc")
        signals = detector.check(step_index=3)
        repeated = [s for s in signals if s.signal_type == DriftSignalType.REPEATED_STEPS]
        assert len(repeated) == 1
        assert repeated[0].severity == DriftSeverity.WARNING

    def test_no_signal_when_fewer_than_threshold_calls(self):
        detector = DriftDetector(original_goal="test goal", max_steps=20)
        detector.record_step(step_index=0, tool_name="grep", params_hash="abc")
        detector.record_step(step_index=1, tool_name="grep", params_hash="abc")
        signals = detector.check(step_index=2)
        repeated = [s for s in signals if s.signal_type == DriftSignalType.REPEATED_STEPS]
        assert len(repeated) == 0

    def test_no_signal_when_tools_vary(self):
        detector = DriftDetector(original_goal="test goal", max_steps=20)
        detector.record_step(step_index=0, tool_name="grep", params_hash="abc")
        detector.record_step(step_index=1, tool_name="read", params_hash="def")
        detector.record_step(step_index=2, tool_name="grep", params_hash="abc")
        signals = detector.check(step_index=3)
        repeated = [s for s in signals if s.signal_type == DriftSignalType.REPEATED_STEPS]
        assert len(repeated) == 0


class TestDriftDetectorSubtaskOverrun:
    def test_warning_when_steps_exceed_budget_ratio(self):
        detector = DriftDetector(original_goal="test", max_steps=10)
        signals = detector.check(step_index=6)
        overrun = [s for s in signals if s.signal_type == DriftSignalType.SUBTASK_OVERRUN]
        assert len(overrun) == 1
        assert overrun[0].severity == DriftSeverity.WARNING

    def test_no_signal_when_steps_within_budget(self):
        detector = DriftDetector(original_goal="test", max_steps=10)
        signals = detector.check(step_index=4)
        overrun = [s for s in signals if s.signal_type == DriftSignalType.SUBTASK_OVERRUN]
        assert len(overrun) == 0

    def test_no_signal_when_step_index_too_small(self):
        detector = DriftDetector(original_goal="test", max_steps=5)
        signals = detector.check(step_index=3)
        overrun = [s for s in signals if s.signal_type == DriftSignalType.SUBTASK_OVERRUN]
        assert len(overrun) == 0

    def test_no_signal_when_max_steps_zero(self):
        detector = DriftDetector(original_goal="test", max_steps=0)
        signals = detector.check(step_index=10)
        overrun = [s for s in signals if s.signal_type == DriftSignalType.SUBTASK_OVERRUN]
        assert len(overrun) == 0


class TestDriftDetectorGoalRewrite:
    def test_warning_when_goal_rewritten_beyond_threshold(self):
        detector = DriftDetector(original_goal="analyze order anomaly cause")
        detector.record_step(step_index=0, goal="cook food tutorial recommend")
        signals = detector.check(step_index=1, current_goal="cook food tutorial recommend")
        rewrite = [s for s in signals if s.signal_type == DriftSignalType.GOAL_REWRITE]
        assert len(rewrite) == 1
        assert rewrite[0].severity == DriftSeverity.WARNING

    def test_no_signal_when_goal_similar_to_original(self):
        detector = DriftDetector(original_goal="analyze order anomaly cause")
        detector.record_step(step_index=0, goal="analyze order data anomaly cause")
        signals = detector.check(step_index=1, current_goal="analyze order data anomaly cause")
        rewrite = [s for s in signals if s.signal_type == DriftSignalType.GOAL_REWRITE]
        assert len(rewrite) == 0

    def test_no_signal_when_only_one_goal_in_history(self):
        detector = DriftDetector(original_goal="analyze order anomaly cause")
        signals = detector.check(step_index=1, current_goal="analyze order anomaly cause")
        rewrite = [s for s in signals if s.signal_type == DriftSignalType.GOAL_REWRITE]
        assert len(rewrite) == 0


class TestDriftDetectorUnusedEvidence:
    def test_warning_when_anomaly_in_result_not_referenced(self):
        detector = DriftDetector(original_goal="test goal", max_steps=20)
        detector.record_step(
            step_index=0, tool_name="read_log", tool_result="发现异常升高现象"
        )
        detector.record_step(
            step_index=1, tool_name="analyze", tool_result="系统错误频率增加"
        )
        detector.record_step(step_index=2, tool_name="grep", params_hash="normal_query")
        detector.record_step(step_index=3, tool_name="read", params_hash="some_file")
        signals = detector.check(step_index=4)
        unused = [s for s in signals if s.signal_type == DriftSignalType.UNUSED_EVIDENCE]
        assert len(unused) == 1

    def test_no_signal_when_no_anomaly_in_results(self):
        detector = DriftDetector(original_goal="test goal", max_steps=20)
        detector.record_step(step_index=0, tool_name="read", tool_result="一切正常")
        detector.record_step(step_index=1, tool_name="grep", tool_result="正常输出")
        signals = detector.check(step_index=2)
        unused = [s for s in signals if s.signal_type == DriftSignalType.UNUSED_EVIDENCE]
        assert len(unused) == 0

    def test_no_signal_when_too_few_results(self):
        detector = DriftDetector(original_goal="test goal", max_steps=20)
        detector.record_step(step_index=0, tool_name="read", tool_result="异常错误")
        signals = detector.check(step_index=1)
        unused = [s for s in signals if s.signal_type == DriftSignalType.UNUSED_EVIDENCE]
        assert len(unused) == 0


class TestDriftDetectorRecordStep:
    def test_records_tool_call_history(self):
        detector = DriftDetector(original_goal="test", max_steps=20)
        detector.record_step(step_index=0, tool_name="grep", params_hash="h1")
        detector.record_step(step_index=1, tool_name="read", params_hash="h2")
        assert len(detector._tool_call_history) == 2
        assert detector._tool_call_history[0] == ("grep", "h1")
        assert detector._tool_call_history[1] == ("read", "h2")

    def test_records_goal_change(self):
        detector = DriftDetector(original_goal="goal A", max_steps=20)
        detector.record_step(step_index=0, goal="goal B")
        assert len(detector._goal_history) == 2
        assert detector._goal_history[-1] == "goal B"

    def test_does_not_duplicate_same_goal(self):
        detector = DriftDetector(original_goal="goal A", max_steps=20)
        detector.record_step(step_index=0, goal="goal A")
        assert len(detector._goal_history) == 1

    def test_records_tool_result(self):
        detector = DriftDetector(original_goal="test", max_steps=20)
        detector.record_step(step_index=0, tool_name="read", tool_result="some result")
        assert len(detector._tool_results) == 1
        assert detector._tool_results[0] == ("read", "some result")

    def test_truncates_long_tool_result(self):
        detector = DriftDetector(original_goal="test", max_steps=20)
        long_result = "x" * 1000
        detector.record_step(step_index=0, tool_name="read", tool_result=long_result)
        assert len(detector._tool_results[0][1]) == 500

    def test_no_tool_recorded_when_name_none(self):
        detector = DriftDetector(original_goal="test", max_steps=20)
        detector.record_step(step_index=0)
        assert len(detector._tool_call_history) == 0


class TestDriftDetectorReport:
    def test_report_property_returns_default_when_none(self):
        detector = DriftDetector(original_goal="test", max_steps=20)
        report = detector.report
        assert isinstance(report, DriftReport)
        assert report.trace_id == ""
        assert report.signals == []
