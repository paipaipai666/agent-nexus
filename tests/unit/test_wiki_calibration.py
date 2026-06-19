"""Tests for agentnexus.wiki.calibration — threshold calibration and confusion matrix."""

from unittest.mock import MagicMock, patch

from agentnexus.wiki.calibration import (
    DEFAULT_THRESHOLDS,
    CalibrationSample,
    ConfusionMatrix,
    evaluate_thresholds,
    run_calibration,
    suggest_threshold_adjustments,
)
from agentnexus.wiki.models import SynthesisLevel


class TestConfusionMatrix:
    def test_default_matrix_initialized_with_zeros(self):
        cm = ConfusionMatrix()
        for actual in cm.labels:
            for predicted in cm.labels:
                assert cm.matrix[actual][predicted] == 0

    def test_add_increments_correct_cell(self):
        cm = ConfusionMatrix()
        cm.add(SynthesisLevel.DIRECT_QUOTE.value, SynthesisLevel.DIRECT_QUOTE.value)
        cm.add(SynthesisLevel.DIRECT_QUOTE.value, SynthesisLevel.PARAPHRASE.value)
        assert cm.matrix[SynthesisLevel.DIRECT_QUOTE.value][SynthesisLevel.DIRECT_QUOTE.value] == 1
        assert cm.matrix[SynthesisLevel.DIRECT_QUOTE.value][SynthesisLevel.PARAPHRASE.value] == 1

    def test_add_ignores_unknown_labels(self):
        cm = ConfusionMatrix()
        cm.add("unknown_actual", SynthesisLevel.DIRECT_QUOTE.value)
        cm.add(SynthesisLevel.DIRECT_QUOTE.value, "unknown_predicted")
        # No crash, no increment
        assert all(
            cm.matrix[a][p] == 0 for a in cm.labels for p in cm.labels
        )

    def test_false_degradation_rate_zero_when_empty(self):
        cm = ConfusionMatrix()
        assert cm.false_degradation_rate() == 0.0

    def test_false_degradation_rate_counts_high_to_low(self):
        cm = ConfusionMatrix()
        # direct_quote predicted as synthesis = degradation
        cm.add(SynthesisLevel.DIRECT_QUOTE.value, SynthesisLevel.SYNTHESIS.value)
        cm.add(SynthesisLevel.DIRECT_QUOTE.value, SynthesisLevel.DIRECT_QUOTE.value)
        rate = cm.false_degradation_rate()
        assert rate == 0.5  # 1 degraded out of 2 total

    def test_miss_rate_zero_when_empty(self):
        cm = ConfusionMatrix()
        assert cm.miss_rate() == 0.0

    def test_miss_rate_counts_low_predicted_as_high(self):
        cm = ConfusionMatrix()
        # synthesis predicted as direct_quote = miss
        cm.add(SynthesisLevel.SYNTHESIS.value, SynthesisLevel.DIRECT_QUOTE.value)
        cm.add(SynthesisLevel.SYNTHESIS.value, SynthesisLevel.SYNTHESIS.value)
        rate = cm.miss_rate()
        assert rate == 0.5  # 1 missed out of 2 total

    def test_to_dict_returns_expected_keys(self):
        cm = ConfusionMatrix()
        cm.add(SynthesisLevel.DIRECT_QUOTE.value, SynthesisLevel.DIRECT_QUOTE.value)
        result = cm.to_dict()
        assert "matrix" in result
        assert "false_degradation_rate" in result
        assert "miss_rate" in result
        assert isinstance(result["false_degradation_rate"], float)
        assert isinstance(result["miss_rate"], float)

    def test_perfect_predictions_have_zero_rates(self):
        cm = ConfusionMatrix()
        for label in cm.labels:
            cm.add(label, label)
        assert cm.false_degradation_rate() == 0.0
        assert cm.miss_rate() == 0.0


class TestCalibrationSample:
    def test_calibration_sample_fields(self):
        sample = CalibrationSample(
            statement_id="s1",
            text="test text",
            source_chunk_ids=["c1"],
            source_texts=["chunk text"],
            human_label=SynthesisLevel.DIRECT_QUOTE.value,
        )
        assert sample.statement_id == "s1"
        assert sample.text == "test text"
        assert sample.source_chunk_ids == ["c1"]
        assert sample.human_label == SynthesisLevel.DIRECT_QUOTE.value


class TestEvaluateThresholds:
    @patch("agentnexus.wiki.calibration.MechanicalVerifier")
    def test_evaluate_thresholds_returns_confusion_matrix(self, MockVerifier):
        mock_verifier = MagicMock()
        mock_verifier.verify_statement.return_value = SynthesisLevel.DIRECT_QUOTE.value
        MockVerifier.return_value = mock_verifier

        samples = [
            CalibrationSample(
                statement_id="s1",
                text="test",
                source_chunk_ids=["c1"],
                source_texts=["chunk"],
                human_label=SynthesisLevel.DIRECT_QUOTE.value,
            )
        ]
        cm = evaluate_thresholds(samples, DEFAULT_THRESHOLDS)
        assert isinstance(cm, ConfusionMatrix)
        assert cm.matrix[SynthesisLevel.DIRECT_QUOTE.value][SynthesisLevel.DIRECT_QUOTE.value] == 1

    @patch("agentnexus.wiki.calibration.MechanicalVerifier")
    def test_evaluate_thresholds_passes_thresholds_to_verifier(self, MockVerifier):
        mock_verifier = MagicMock()
        mock_verifier.verify_statement.return_value = SynthesisLevel.SYNTHESIS.value
        MockVerifier.return_value = mock_verifier

        custom = {"jaccard_direct_quote": 0.8}
        samples = [
            CalibrationSample("s1", "t", ["c1"], ["ct"], SynthesisLevel.DIRECT_QUOTE.value)
        ]
        evaluate_thresholds(samples, custom)
        MockVerifier.assert_called_once_with(thresholds=custom)


class TestSuggestThresholdAdjustments:
    def test_no_adjustments_when_matrix_balanced(self):
        cm = ConfusionMatrix()
        # All correct predictions
        for label in cm.labels:
            cm.add(label, label)
        current = dict(DEFAULT_THRESHOLDS)
        adjusted = suggest_threshold_adjustments(cm, current)
        assert adjusted == current

    def test_lowers_jaccard_direct_quote_on_high_miss_rate(self):
        cm = ConfusionMatrix()
        # Direct quote consistently predicted as paraphrase
        for _ in range(10):
            cm.add(SynthesisLevel.DIRECT_QUOTE.value, SynthesisLevel.PARAPHRASE.value)
        current = dict(DEFAULT_THRESHOLDS)
        adjusted = suggest_threshold_adjustments(cm, current)
        assert adjusted["jaccard_direct_quote"] < current["jaccard_direct_quote"]

    def test_lowers_cosine_paraphrase_on_high_degradation(self):
        cm = ConfusionMatrix()
        # Paraphrase consistently predicted as cross_reference
        for _ in range(10):
            cm.add(SynthesisLevel.PARAPHRASE.value, SynthesisLevel.CROSS_REFERENCE.value)
        current = dict(DEFAULT_THRESHOLDS)
        adjusted = suggest_threshold_adjustments(cm, current)
        assert adjusted["cosine_paraphrase"] < current["cosine_paraphrase"]

    def test_lowers_cosine_source_on_cross_ref_degradation(self):
        cm = ConfusionMatrix()
        for _ in range(10):
            cm.add(SynthesisLevel.CROSS_REFERENCE.value, SynthesisLevel.SYNTHESIS.value)
        current = dict(DEFAULT_THRESHOLDS)
        adjusted = suggest_threshold_adjustments(cm, current)
        assert adjusted["cosine_source"] < current["cosine_source"]

    def test_raises_jaccard_on_false_positive_direct_quote(self):
        cm = ConfusionMatrix()
        # Many non-DQ predicted as DQ (false positives)
        for _ in range(10):
            cm.add(SynthesisLevel.PARAPHRASE.value, SynthesisLevel.DIRECT_QUOTE.value)
        # Also some real DQ to have a baseline
        for _ in range(2):
            cm.add(SynthesisLevel.DIRECT_QUOTE.value, SynthesisLevel.DIRECT_QUOTE.value)
        current = dict(DEFAULT_THRESHOLDS)
        adjusted = suggest_threshold_adjustments(cm, current)
        assert adjusted["jaccard_direct_quote"] > current["jaccard_direct_quote"]


class TestRunCalibration:
    @patch("agentnexus.wiki.calibration.evaluate_thresholds")
    def test_run_calibration_returns_expected_keys(self, mock_eval):
        mock_cm = ConfusionMatrix()
        for label in mock_cm.labels:
            mock_cm.add(label, label)
        mock_eval.return_value = mock_cm

        mock_store = MagicMock()
        samples = [
            CalibrationSample("s1", "text", ["c1"], ["chunk"], SynthesisLevel.DIRECT_QUOTE.value)
        ]
        result = run_calibration(mock_store, samples, max_rounds=1)
        assert "thresholds" in result
        assert "confusion_matrix" in result
        assert "sample_size" in result
        assert "rounds" in result
        assert result["sample_size"] == 1

    @patch("agentnexus.wiki.calibration.evaluate_thresholds")
    def test_run_calibration_stores_results(self, mock_eval):
        mock_cm = ConfusionMatrix()
        for label in mock_cm.labels:
            mock_cm.add(label, label)
        mock_eval.return_value = mock_cm

        mock_store = MagicMock()
        samples = [
            CalibrationSample("s1", "text", ["c1"], ["chunk"], SynthesisLevel.DIRECT_QUOTE.value)
        ]
        run_calibration(mock_store, samples, max_rounds=1)
        mock_store.save_calibration.assert_called_once()

    @patch("agentnexus.wiki.calibration.evaluate_thresholds")
    def test_run_calibration_stops_early_on_good_score(self, mock_eval):
        mock_cm = ConfusionMatrix()
        for label in mock_cm.labels:
            mock_cm.add(label, label)
        mock_eval.return_value = mock_cm

        mock_store = MagicMock()
        samples = [
            CalibrationSample("s1", "text", ["c1"], ["chunk"], SynthesisLevel.DIRECT_QUOTE.value)
        ]
        result = run_calibration(mock_store, samples, max_rounds=5)
        # Perfect score < 0.1, should stop after 1 round
        assert result["rounds"] == 1
