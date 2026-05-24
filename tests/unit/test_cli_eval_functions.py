import json
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from agentnexus.cli import app
from agentnexus.cli.eval_cmd import _compute_calibration, _fmt_ci, _pearson, _spearman

runner = CliRunner()


class MockSample:
    def __init__(self, question, ground_truth):
        self.question = question
        self.ground_truth = ground_truth


class TestFmtCi:
    def test_without_ci(self):
        assert _fmt_ci(0.5) == "0.500"

    def test_with_ci(self):
        assert _fmt_ci(0.5, (0.40, 0.60)) == "0.500 [0.40-0.60]"

    def test_with_none_ci(self):
        assert _fmt_ci(0.5, None) == "0.500"


class TestSpearman:
    def test_n_less_than_3(self):
        assert _spearman([1.0, 2.0], [2.0, 1.0]) == (0.0, 1.0)

    def test_perfect_correlation(self):
        x = [1.0, 2.0, 3.0, 4.0, 5.0]
        rho, p = _spearman(x, x)
        assert rho == pytest.approx(1.0)

    def test_negative_correlation(self):
        x = [1.0, 2.0, 3.0]
        y = [3.0, 2.0, 1.0]
        rho, p = _spearman(x, y)
        assert rho == -1.0


class TestPearson:
    def test_n_less_than_3(self):
        assert _pearson([1.0, 2.0], [2.0, 1.0]) == (0.0, 1.0)

    def test_perfect_correlation(self):
        x = [1.0, 2.0, 3.0, 4.0, 5.0]
        r, p = _pearson(x, x)
        assert r == 1.0

    def test_denominator_zero(self):
        r, p = _pearson([1.0, 1.0, 1.0, 1.0], [2.0, 2.0, 2.0, 2.0])
        assert r == 0.0
        assert p == 1.0


class TestComputeCalibration:
    def test_file_not_found(self, capsys):
        _compute_calibration([], "nonexistent_file.json")
        captured = capsys.readouterr()
        assert "评分文件不存在" in captured.out

    def test_parse_error(self, temp_agentnexus_home, capsys):
        score_file = temp_agentnexus_home / "bad.json"
        score_file.write_text("not valid json", encoding="utf-8")
        _compute_calibration([], str(score_file))
        captured = capsys.readouterr()
        assert "读取评分文件失败" in captured.out

    def test_normal(self, temp_agentnexus_home, capsys):
        samples = [
            {"sample_idx": i, "judge_precision": jp, "judge_recall": jr}
            for i, (jp, jr) in enumerate(
                [
                    (0.9, 0.8),
                    (0.7, 0.6),
                    (0.5, 0.4),
                    (0.3, 0.2),
                ]
            )
        ]
        human_scores = [
            {"sample_idx": i, "human_precision": hp, "human_recall": hr}
            for i, (hp, hr) in enumerate(
                [
                    (0.85, 0.78),
                    (0.72, 0.58),
                    (0.48, 0.42),
                    (0.32, 0.22),
                ]
            )
        ]

        score_file = temp_agentnexus_home / "scores.json"
        score_file.write_text(json.dumps(human_scores), encoding="utf-8")

        _compute_calibration(samples, str(score_file))
        captured = capsys.readouterr()

        assert "校准结果" in captured.out
        assert "Spearman" in captured.out
        assert "Pearson" in captured.out
        assert "Precision" in captured.out
        assert "Recall" in captured.out


class TestEvalListCommand:
    @patch(
        "agentnexus.rag.eval_dataset.EVAL_SAMPLES",
        [
            MockSample("What is Qdrant?", "Qdrant uses HNSW"),
            MockSample("What is BM25?", "BM25 is a ranking function"),
        ],
    )
    @patch("agentnexus.rag.eval_dataset.KNOWLEDGE_BASE", ["doc1", "doc2"])
    def test_eval_list(self):
        result = runner.invoke(app, ["eval", "list"])
        assert result.exit_code == 0
        assert "What is Qdrant?" in result.output
        assert "What is BM25?" in result.output
        assert "知识库类型" in result.output
