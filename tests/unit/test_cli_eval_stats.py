"""Tests for agentnexus.cli.eval.stats: calibration and correlation helpers."""

import json

import pytest

from agentnexus.cli.eval.stats import _compute_calibration, _pearson, _spearman


class TestSpearman:
    """Tests for _spearman."""

    def test_n_less_than_3(self):
        assert _spearman([1.0, 2.0], [2.0, 1.0]) == (0.0, 1.0)

    def test_perfect_correlation(self):
        x = [1.0, 2.0, 3.0, 4.0, 5.0]
        rho, _p = _spearman(x, x)
        assert rho == pytest.approx(1.0, abs=1e-3)

    def test_inverse_correlation(self):
        x = [1.0, 2.0, 3.0]
        y = [3.0, 2.0, 1.0]
        rho, _p = _spearman(x, y)
        assert rho == pytest.approx(-1.0, abs=1e-3)

    def test_moderate_correlation(self):
        x = [1.0, 2.0, 3.0, 4.0, 5.0]
        y = [1.1, 1.9, 3.2, 3.8, 5.1]
        rho, _p = _spearman(x, y)
        assert 0.8 < rho <= 1.0


class TestPearson:
    """Tests for _pearson."""

    def test_n_less_than_3(self):
        assert _pearson([1.0, 2.0], [2.0, 1.0]) == (0.0, 1.0)

    def test_constant_x(self):
        assert _pearson([5.0, 5.0, 5.0], [1.0, 2.0, 3.0]) == (0.0, 1.0)

    def test_constant_y(self):
        assert _pearson([1.0, 2.0, 3.0], [5.0, 5.0, 5.0]) == (0.0, 1.0)

    def test_perfect_positive(self):
        x = [1.0, 2.0, 3.0, 4.0, 5.0]
        r, _p = _pearson(x, x)
        assert r == pytest.approx(1.0, abs=1e-4)

    def test_perfect_negative(self):
        x = [1.0, 2.0, 3.0]
        y = [3.0, 2.0, 1.0]
        r, _p = _pearson(x, y)
        assert r == pytest.approx(-1.0, abs=1e-4)

    def test_zero_denominator(self):
        r, p = _pearson([1.0, 1.0, 1.0, 1.0], [2.0, 2.0, 2.0, 2.0])
        assert r == 0.0
        assert p == 1.0


class TestComputeCalibration:
    """Tests for _compute_calibration."""

    def test_score_file_not_found(self, mocker):
        mock_console = mocker.MagicMock()
        mocker.patch("agentnexus.cli.eval.stats.console", mock_console)

        _compute_calibration([], "nonexistent_file.json")

        printed = " ".join(
            str(args[0]) for args, _ in mock_console.print.call_args_list
        )
        assert "not found" in printed.lower()

    def test_malformed_json(self, tmp_path, mocker):
        mock_console = mocker.MagicMock()
        mocker.patch("agentnexus.cli.eval.stats.console", mock_console)

        score_file = tmp_path / "bad.json"
        score_file.write_text("not valid json", encoding="utf-8")

        _compute_calibration([], str(score_file))

        printed = " ".join(
            str(args[0]) for args, _ in mock_console.print.call_args_list
        )
        assert "failed" in printed.lower()

    def test_no_human_scores_too_few_samples(self, tmp_path, mocker):
        mock_console = mocker.MagicMock()
        mocker.patch("agentnexus.cli.eval.stats.console", mock_console)

        score_file = tmp_path / "scores.json"
        score_file.write_text("[]", encoding="utf-8")

        samples = [
            {"sample_idx": 0, "judge_precision": 0.9, "judge_recall": 0.8},
        ]

        _compute_calibration(samples, str(score_file))

        printed = " ".join(
            str(args[0]) for args, _ in mock_console.print.call_args_list
        )
        assert "too few samples" in printed.lower()

    def test_happy_path_with_enough_samples(self, tmp_path, mocker):
        mock_console = mocker.MagicMock()
        mocker.patch("agentnexus.cli.eval.stats.console", mock_console)

        human_scores = [
            {"sample_idx": i, "human_precision": hp, "human_recall": hr}
            for i, (hp, hr) in enumerate(
                [(0.85, 0.78), (0.72, 0.58), (0.48, 0.42), (0.32, 0.22)]
            )
        ]
        score_file = tmp_path / "scores.json"
        score_file.write_text(json.dumps(human_scores), encoding="utf-8")

        samples = [
            {"sample_idx": i, "judge_precision": jp, "judge_recall": jr}
            for i, (jp, jr) in enumerate(
                [(0.9, 0.8), (0.7, 0.6), (0.5, 0.4), (0.3, 0.2)]
            )
        ]

        _compute_calibration(samples, str(score_file))

        printed = " ".join(
            str(args[0]) for args, _ in mock_console.print.call_args_list
        )
        assert "precision" in printed.lower()
        assert "recall" in printed.lower()
        assert "spearman" in printed.lower()
        assert "pearson" in printed.lower()

    def test_only_precision_scores(self, tmp_path, mocker):
        """Human scores only have precision, no recall."""
        mock_console = mocker.MagicMock()
        mocker.patch("agentnexus.cli.eval.stats.console", mock_console)

        human_scores = [
            {"sample_idx": i, "human_precision": hp, "human_recall": None}
            for i, hp in enumerate([0.85, 0.72, 0.48, 0.32])
        ]
        score_file = tmp_path / "scores.json"
        score_file.write_text(json.dumps(human_scores), encoding="utf-8")

        samples = [
            {"sample_idx": i, "judge_precision": jp, "judge_recall": jr}
            for i, (jp, jr) in enumerate(
                [(0.9, 0.8), (0.7, 0.6), (0.5, 0.4), (0.3, 0.2)]
            )
        ]

        _compute_calibration(samples, str(score_file))

        printed = " ".join(
            str(args[0]) for args, _ in mock_console.print.call_args_list
        )
        # Precision section should have correlation computed (>= 3 samples)
        assert "precision" in printed.lower()
        assert "spearman" in printed.lower()

    def test_only_recall_scores(self, tmp_path, mocker):
        """Human scores only have recall, no precision."""
        mock_console = mocker.MagicMock()
        mocker.patch("agentnexus.cli.eval.stats.console", mock_console)

        human_scores = [
            {"sample_idx": i, "human_precision": None, "human_recall": hr}
            for i, hr in enumerate([0.78, 0.58, 0.42, 0.22])
        ]
        score_file = tmp_path / "scores.json"
        score_file.write_text(json.dumps(human_scores), encoding="utf-8")

        samples = [
            {"sample_idx": i, "judge_precision": jp, "judge_recall": jr}
            for i, (jp, jr) in enumerate(
                [(0.9, 0.8), (0.7, 0.6), (0.5, 0.4), (0.3, 0.2)]
            )
        ]

        _compute_calibration(samples, str(score_file))

        printed = " ".join(
            str(args[0]) for args, _ in mock_console.print.call_args_list
        )
        # Recall section should have correlation computed
        assert "recall" in printed.lower()
        assert "spearman" in printed.lower()
