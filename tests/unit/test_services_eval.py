"""Tests for EvalService."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from agentnexus.services.eval import EvalService


class TestEvalService:
    def test_run_rag_eval_instantiates_evaluator(self):
        with patch("agentnexus.rag.evaluator.RAGEvaluator") as mock_eval_class:
            service = EvalService(MagicMock())
            service.run_rag_eval("test_dataset", chunk_size=256)

        mock_eval_class.assert_called_once_with("test_dataset", chunk_size=256)

    def test_run_rag_eval_no_args(self):
        with patch("agentnexus.rag.evaluator.RAGEvaluator") as mock_eval_class:
            service = EvalService(MagicMock())
            service.run_rag_eval()

        mock_eval_class.assert_called_once_with()

    def test_list_reports_empty_when_traces_dir_missing(self):
        settings = MagicMock()
        settings.traces_dir = "/nonexistent/path"
        service = EvalService(settings)
        assert service.list_reports() == []

    def test_list_reports_returns_sorted_jsonl_files(self, tmp_path):
        settings = MagicMock()
        traces = tmp_path / "traces"
        traces.mkdir()
        (traces / "b.jsonl").write_text("{}")
        (traces / "a.jsonl").write_text("{}")
        (traces / "c.jsonl").write_text("{}")
        settings.traces_dir = str(traces)

        service = EvalService(settings)
        reports = service.list_reports()

        assert len(reports) == 3
        assert reports == sorted(reports)
        assert all(r.suffix == ".jsonl" for r in reports)

    def test_compare_reports_returns_paths(self):
        service = EvalService(MagicMock())
        result = service.compare_reports("/path/a.jsonl", "/path/b.jsonl")
        assert result == {"left": "/path/a.jsonl", "right": "/path/b.jsonl"}

    def test_compare_reports_accepts_path_objects(self):
        service = EvalService(MagicMock())
        result = service.compare_reports(Path("/a.jsonl"), Path("/b.jsonl"))
        assert result == {"left": str(Path("/a.jsonl")), "right": str(Path("/b.jsonl"))}
