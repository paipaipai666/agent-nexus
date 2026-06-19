"""Tests for agentnexus.observability.drift_detector."""
import time

import pytest

from agentnexus.observability.drift_detector import (
    DriftDetector,
    DriftReport,
    DriftSeverity,
    DriftSignal,
    DriftSignalType,
    _keyword_overlap,
)


# ── _keyword_overlap ─────────────────────────────────────────────


class TestKeywordOverlap:
    def test_identical_text_returns_one(self):
        # Arrange
        text = "分析订单异常原因"
        # Act
        score = _keyword_overlap(text, text)
        # Assert
        assert score == 1.0

    def test_completely_different_text_returns_zero(self):
        # Arrange
        text_a = "分析订单异常原因"
        text_b = "hello world foo bar"
        # Act
        score = _keyword_overlap(text_a, text_b)
        # Assert
        assert score == 0.0

    def test_partial_overlap_returns_proportion(self):
        # Arrange — "分析 订单 异常 原因" vs "分析 数据 异常 报告"
        # intersection: {分析, 异常}, union: {分析, 订单, 异常, 原因, 数据, 报告} = 6
        text_a = "分析 订单 异常 原因"
        text_b = "分析 数据 异常 报告"
        # Act
        score = _keyword_overlap(text_a, text_b)
        # Assert
        assert score == pytest.approx(2 / 6)

    def test_stop_words_are_filtered(self):
        # Arrange — "the" and "is" are stop words
        text_a = "the cat is happy"
        text_b = "the dog is sad"
        # After filtering: {cat, happy} vs {dog, sad} → no overlap
        # Act
        score = _keyword_overlap(text_a, text_b)
        # Assert
        assert score == 0.0

    def test_empty_text_returns_zero(self):
        # Arrange & Act
        score = _keyword_overlap("", "hello world")
        # Assert
        assert score == 0.0

    def test_both_empty_returns_zero(self):
        # Arrange & Act
        score = _keyword_overlap("", "")
        # Assert
        assert score == 0.0


# ── DriftReport ──────────────────────────────────────────────────


class TestDriftReport:
    def test_passed_true_when_no_critical_signals(self):
        # Arrange
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
        # Act & Assert
        assert report.passed is True

    def test_passed_false_when_critical_signal_present(self):
        # Arrange
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
        # Act & Assert
        assert report.passed is False

    def test_warning_count(self):
        # Arrange
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
        # Act & Assert
        assert report.warning_count == 2

    def test_critical_count(self):
        # Arrange
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
        # Act & Assert
        assert report.critical_count == 2

    def test_passed_true_when_no_signals(self):
        # Arrange
        report = DriftReport(trace_id="t1")
        # Act & Assert
        assert report.passed is True
        assert report.warning_count == 0
        assert report.critical_count == 0


# ── DriftDetector — goal relevance ───────────────────────────────


class TestDriftDetectorGoalRelevance:
    def test_no_signal_when_goal_highly_relevant(self):
        # Arrange
        detector = DriftDetector(original_goal="分析订单异常原因")
        # Act
        signals = detector.check(step_index=1, current_goal="分析订单异常原因")
        # Assert
        drift_signals = [s for s in signals if s.signal_type == DriftSignalType.GOAL_DRIFT]
        assert len(drift_signals) == 0

    def test_warning_signal_when_goal_low_relevance(self):
        # Arrange — need overlap between RELEVANCE_CRITICAL (0.05) and RELEVANCE_THRESHOLD (0.15)
        # _keyword_overlap uses str.split(), so text must be space-separated.
        # "alpha beta gamma delta epsilon" → 5 tokens
        # "alpha zeta eta theta iota kappa" → 6 tokens, 1 shared (alpha)
        # overlap = 1 / 10 = 0.1, which is < 0.15 (WARNING) but >= 0.05 (not CRITICAL)
        detector = DriftDetector(original_goal="alpha beta gamma delta epsilon")
        # Act
        signals = detector.check(step_index=1, current_goal="alpha zeta eta theta iota kappa")
        # Assert
        drift_signals = [s for s in signals if s.signal_type == DriftSignalType.GOAL_DRIFT]
        assert len(drift_signals) == 1
        assert drift_signals[0].severity == DriftSeverity.WARNING

    def test_critical_signal_when_goal_very_low_relevance(self):
        # Arrange
        detector = DriftDetector(original_goal="分析订单异常原因")
        # Act — gibberish, no overlap at all
        signals = detector.check(step_index=1, current_goal="xyz abc def ghi")
        # Assert
        drift_signals = [s for s in signals if s.signal_type == DriftSignalType.GOAL_DRIFT]
        assert len(drift_signals) == 1
        assert drift_signals[0].severity == DriftSeverity.CRITICAL

    def test_no_signal_when_current_goal_empty(self):
        # Arrange
        detector = DriftDetector(original_goal="分析订单异常原因")
        # Act
        signals = detector.check(step_index=1, current_goal="")
        # Assert
        drift_signals = [s for s in signals if s.signal_type == DriftSignalType.GOAL_DRIFT]
        assert len(drift_signals) == 0


# ── DriftDetector — repeated steps ───────────────────────────────


class TestDriftDetectorRepeatedSteps:
    def test_warning_when_consecutive_identical_tool_calls(self):
        # Arrange
        detector = DriftDetector(original_goal="test goal", max_steps=20)
        for i in range(3):
            detector.record_step(step_index=i, tool_name="grep", params_hash="abc")
        # Act
        signals = detector.check(step_index=3)
        # Assert
        repeated = [s for s in signals if s.signal_type == DriftSignalType.REPEATED_STEPS]
        assert len(repeated) == 1
        assert repeated[0].severity == DriftSeverity.WARNING

    def test_no_signal_when_fewer_than_threshold_calls(self):
        # Arrange
        detector = DriftDetector(original_goal="test goal", max_steps=20)
        detector.record_step(step_index=0, tool_name="grep", params_hash="abc")
        detector.record_step(step_index=1, tool_name="grep", params_hash="abc")
        # Act — only 2 calls, threshold is 3
        signals = detector.check(step_index=2)
        # Assert
        repeated = [s for s in signals if s.signal_type == DriftSignalType.REPEATED_STEPS]
        assert len(repeated) == 0

    def test_no_signal_when_tools_vary(self):
        # Arrange
        detector = DriftDetector(original_goal="test goal", max_steps=20)
        detector.record_step(step_index=0, tool_name="grep", params_hash="abc")
        detector.record_step(step_index=1, tool_name="read", params_hash="def")
        detector.record_step(step_index=2, tool_name="grep", params_hash="abc")
        # Act
        signals = detector.check(step_index=3)
        # Assert
        repeated = [s for s in signals if s.signal_type == DriftSignalType.REPEATED_STEPS]
        assert len(repeated) == 0


# ── DriftDetector — subtask overrun ──────────────────────────────


class TestDriftDetectorSubtaskOverrun:
    def test_warning_when_steps_exceed_budget_ratio(self):
        # Arrange — max_steps=10, SUBTASK_OVERRUN_RATIO=0.5
        # step_index=6 → ratio=0.6 > 0.5 and > 3
        detector = DriftDetector(original_goal="test", max_steps=10)
        # Act
        signals = detector.check(step_index=6)
        # Assert
        overrun = [s for s in signals if s.signal_type == DriftSignalType.SUBTASK_OVERRUN]
        assert len(overrun) == 1
        assert overrun[0].severity == DriftSeverity.WARNING

    def test_no_signal_when_steps_within_budget(self):
        # Arrange — step_index=4, max_steps=10 → ratio=0.4 < 0.5
        detector = DriftDetector(original_goal="test", max_steps=10)
        # Act
        signals = detector.check(step_index=4)
        # Assert
        overrun = [s for s in signals if s.signal_type == DriftSignalType.SUBTASK_OVERRUN]
        assert len(overrun) == 0

    def test_no_signal_when_step_index_too_small(self):
        # Arrange — step_index=3, even if ratio > 0.5, skipped because <= 3
        detector = DriftDetector(original_goal="test", max_steps=5)
        # Act — step_index=3, ratio=0.6 > 0.5, but step_index <= 3
        signals = detector.check(step_index=3)
        # Assert
        overrun = [s for s in signals if s.signal_type == DriftSignalType.SUBTASK_OVERRUN]
        assert len(overrun) == 0

    def test_no_signal_when_max_steps_zero(self):
        # Arrange
        detector = DriftDetector(original_goal="test", max_steps=0)
        # Act
        signals = detector.check(step_index=10)
        # Assert
        overrun = [s for s in signals if s.signal_type == DriftSignalType.SUBTASK_OVERRUN]
        assert len(overrun) == 0


# ── DriftDetector — goal rewrite ─────────────────────────────────


class TestDriftDetectorGoalRewrite:
    def test_warning_when_goal_rewritten_beyond_threshold(self):
        # Arrange — _keyword_overlap uses str.split(), so use space-separated tokens
        # "analyze order anomaly cause" vs "cook food tutorial recommend" → 0 overlap → < 0.3
        detector = DriftDetector(original_goal="analyze order anomaly cause")
        detector.record_step(step_index=0, goal="cook food tutorial recommend")
        # Act
        signals = detector.check(step_index=1, current_goal="cook food tutorial recommend")
        # Assert
        rewrite = [s for s in signals if s.signal_type == DriftSignalType.GOAL_REWRITE]
        assert len(rewrite) == 1
        assert rewrite[0].severity == DriftSeverity.WARNING

    def test_no_signal_when_goal_similar_to_original(self):
        # Arrange — high overlap: "analyze order anomaly cause" vs "analyze order data anomaly cause"
        # tokens_a = {analyze, order, anomaly, cause}, tokens_b = {analyze, order, data, anomaly, cause}
        # intersection = 4, union = 5, similarity = 0.8 > 0.3
        detector = DriftDetector(original_goal="analyze order anomaly cause")
        detector.record_step(step_index=0, goal="analyze order data anomaly cause")
        # Act
        signals = detector.check(step_index=1, current_goal="analyze order data anomaly cause")
        # Assert
        rewrite = [s for s in signals if s.signal_type == DriftSignalType.GOAL_REWRITE]
        assert len(rewrite) == 0

    def test_no_signal_when_only_one_goal_in_history(self):
        # Arrange — no goal changes recorded
        detector = DriftDetector(original_goal="analyze order anomaly cause")
        # Act
        signals = detector.check(step_index=1, current_goal="analyze order anomaly cause")
        # Assert
        rewrite = [s for s in signals if s.signal_type == DriftSignalType.GOAL_REWRITE]
        assert len(rewrite) == 0


# ── DriftDetector — unused evidence ──────────────────────────────


class TestDriftDetectorUnusedEvidence:
    def test_warning_when_anomaly_in_result_not_referenced(self):
        # Arrange
        detector = DriftDetector(original_goal="test goal", max_steps=20)
        # Record tool results with anomaly keywords
        detector.record_step(
            step_index=0, tool_name="read_log", tool_result="发现异常升高现象"
        )
        detector.record_step(
            step_index=1, tool_name="analyze", tool_result="系统错误频率增加"
        )
        # Record subsequent tool calls that do NOT reference anomaly keywords
        detector.record_step(step_index=2, tool_name="grep", params_hash="normal_query")
        detector.record_step(step_index=3, tool_name="read", params_hash="some_file")
        # Act
        signals = detector.check(step_index=4)
        # Assert
        unused = [s for s in signals if s.signal_type == DriftSignalType.UNUSED_EVIDENCE]
        assert len(unused) == 1

    def test_no_signal_when_no_anomaly_in_results(self):
        # Arrange
        detector = DriftDetector(original_goal="test goal", max_steps=20)
        detector.record_step(step_index=0, tool_name="read", tool_result="一切正常")
        detector.record_step(step_index=1, tool_name="grep", tool_result="正常输出")
        # Act
        signals = detector.check(step_index=2)
        # Assert
        unused = [s for s in signals if s.signal_type == DriftSignalType.UNUSED_EVIDENCE]
        assert len(unused) == 0

    def test_no_signal_when_too_few_results(self):
        # Arrange — fewer than 2 tool results
        detector = DriftDetector(original_goal="test goal", max_steps=20)
        detector.record_step(step_index=0, tool_name="read", tool_result="异常错误")
        # Act
        signals = detector.check(step_index=1)
        # Assert
        unused = [s for s in signals if s.signal_type == DriftSignalType.UNUSED_EVIDENCE]
        assert len(unused) == 0


# ── DriftDetector — record_step ──────────────────────────────────


class TestDriftDetectorRecordStep:
    def test_records_tool_call_history(self):
        # Arrange
        detector = DriftDetector(original_goal="test", max_steps=20)
        # Act
        detector.record_step(step_index=0, tool_name="grep", params_hash="h1")
        detector.record_step(step_index=1, tool_name="read", params_hash="h2")
        # Assert
        assert len(detector._tool_call_history) == 2
        assert detector._tool_call_history[0] == ("grep", "h1")
        assert detector._tool_call_history[1] == ("read", "h2")

    def test_records_goal_change(self):
        # Arrange
        detector = DriftDetector(original_goal="goal A", max_steps=20)
        # Act
        detector.record_step(step_index=0, goal="goal B")
        # Assert
        assert len(detector._goal_history) == 2
        assert detector._goal_history[-1] == "goal B"

    def test_does_not_duplicate_same_goal(self):
        # Arrange
        detector = DriftDetector(original_goal="goal A", max_steps=20)
        # Act
        detector.record_step(step_index=0, goal="goal A")
        # Assert
        assert len(detector._goal_history) == 1

    def test_records_tool_result(self):
        # Arrange
        detector = DriftDetector(original_goal="test", max_steps=20)
        # Act
        detector.record_step(step_index=0, tool_name="read", tool_result="some result")
        # Assert
        assert len(detector._tool_results) == 1
        assert detector._tool_results[0] == ("read", "some result")

    def test_truncates_long_tool_result(self):
        # Arrange
        detector = DriftDetector(original_goal="test", max_steps=20)
        long_result = "x" * 1000
        # Act
        detector.record_step(step_index=0, tool_name="read", tool_result=long_result)
        # Assert
        assert len(detector._tool_results[0][1]) == 500

    def test_no_tool_recorded_when_name_none(self):
        # Arrange
        detector = DriftDetector(original_goal="test", max_steps=20)
        # Act
        detector.record_step(step_index=0)
        # Assert
        assert len(detector._tool_call_history) == 0


# ── DriftDetector — report property ──────────────────────────────


class TestDriftDetectorReport:
    def test_report_property_returns_default_when_none(self):
        # Arrange
        detector = DriftDetector(original_goal="test", max_steps=20)
        # Act
        report = detector.report
        # Assert
        assert isinstance(report, DriftReport)
        assert report.trace_id == ""
        assert report.signals == []
