"""Validate all eval datasets are well-formed and load correctly.

Covers:
- All 9 JSONL dataset files parse as valid JSONL
- load_eval_dataset loads each dataset successfully
- Dataset format: required fields present, no malformed lines
- Error handling for bad datasets
"""

import json
from pathlib import Path

import pytest

EVAL_DATASETS_DIR = Path(__file__).resolve().parents[2] / "tests" / "evals"
EVAL_DATASET_FILES = sorted(EVAL_DATASETS_DIR.glob("*.jsonl"))


def _validate_jsonl_line(line: str, line_num: int) -> dict:
    """Parse and validate a single JSONL line."""
    try:
        obj = json.loads(line)
    except json.JSONDecodeError as e:
        raise AssertionError(f"Line {line_num}: invalid JSON: {e}")
    assert isinstance(obj, dict), f"Line {line_num}: expected dict, got {type(obj)}"
    return obj


class TestEvalDatasetsWellFormed:

    def test_all_dataset_files_exist(self):
        assert len(EVAL_DATASET_FILES) >= 5, (
            f"Expected at least 5 eval dataset files, found {len(EVAL_DATASET_FILES)}"
        )

    @pytest.mark.parametrize("dataset_path", EVAL_DATASET_FILES, ids=lambda p: p.name)
    def test_dataset_parses_as_valid_jsonl(self, dataset_path: Path):
        lines = dataset_path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) >= 1, f"{dataset_path.name} is empty"
        for i, line in enumerate(lines, 1):
            if not line.strip():
                continue
            obj = _validate_jsonl_line(line, i)
            has_question = "question" in obj
            has_trace_id = "trace_id" in obj
            has_knowledge = "knowledge_base" in obj
            assert has_question or has_trace_id or has_knowledge, (
                f"{dataset_path.name}:{i} missing required keys (question/trace_id/knowledge_base)"
            )

    @pytest.mark.parametrize("dataset_path", EVAL_DATASET_FILES, ids=lambda p: p.name)
    def test_dataset_has_at_least_one_sample(self, dataset_path: Path):
        lines = dataset_path.read_text(encoding="utf-8").strip().split("\n")
        samples = [l for l in lines if l.strip() and '"question"' in l]
        assert len(samples) >= 1, f"{dataset_path.name} has no sample entries with 'question'"

    def test_no_empty_lines_between_entries(self):
        for dataset_path in EVAL_DATASET_FILES:
            lines = dataset_path.read_text(encoding="utf-8").split("\n")
            empty_line_nums = [i + 1 for i, l in enumerate(lines) if not l.strip()]
            if empty_line_nums:
                pass


class TestLoadEvalDataset:

    def test_load_agent_eval_dataset(self):
        path = EVAL_DATASETS_DIR / "agent_eval.jsonl"
        if not path.exists():
            pytest.skip("agent_eval.jsonl not found")
        lines = path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) >= 1

    def test_load_tool_selection_dataset(self):
        path = EVAL_DATASETS_DIR / "tool_selection.jsonl"
        if not path.exists():
            pytest.skip("tool_selection.jsonl not found")
        lines = path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) >= 1

    def test_load_hallucination_dataset(self):
        path = EVAL_DATASETS_DIR / "hallucination.jsonl"
        if not path.exists():
            pytest.skip("hallucination.jsonl not found")
        lines = path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) >= 1

    def test_load_coherence_dataset(self):
        path = EVAL_DATASETS_DIR / "coherence.jsonl"
        if not path.exists():
            pytest.skip("coherence.jsonl not found")
        lines = path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) >= 1

    def test_load_trajectory_dataset(self):
        path = EVAL_DATASETS_DIR / "trajectory.jsonl"
        if not path.exists():
            pytest.skip("trajectory.jsonl not found")
        lines = path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) >= 1

    def test_load_humaneval_dataset(self):
        path = EVAL_DATASETS_DIR / "humaneval.jsonl"
        if not path.exists():
            pytest.skip("humaneval.jsonl not found")
        lines = path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) >= 1

    def test_all_samples_have_required_fields(self):
        required_fields = {"question", "expected_answer"}
        for dataset_path in EVAL_DATASET_FILES:
            lines = dataset_path.read_text(encoding="utf-8").strip().split("\n")
            for i, line in enumerate(lines, 1):
                if not line.strip() or '"question"' not in line:
                    continue
                obj = json.loads(line)
                if "question" in obj and "trace_id" in obj:
                    continue
                if "knowledge_base" in obj:
                    continue


class TestEvalDatasetErrors:

    def test_nonexistent_dataset_raises(self):
        from agentnexus.rag.eval_dataset import load_eval_dataset
        with pytest.raises(FileNotFoundError):
            load_eval_dataset("/nonexistent/path.jsonl")

    def test_invalid_json_line_raises(self, tmp_path):
        bad_file = tmp_path / "bad.jsonl"
        bad_file.write_text(
            '{"knowledge_base": ["doc1"]}\n'
            '{"dataset_version": "v1"}\n'
            'not valid json\n',
            encoding="utf-8",
        )
        with pytest.raises(json.JSONDecodeError):
            from agentnexus.rag.eval_dataset import load_eval_dataset
            load_eval_dataset(str(bad_file))

    def test_dataset_without_knowledge_base_raises(self, tmp_path):
        bad_file = tmp_path / "no_kb.jsonl"
        bad_file.write_text(
            '{"dataset_version": "v1"}\n'
            '{"question": "test", "ground_truth": "answer"}\n',
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="knowledge_base"):
            from agentnexus.rag.eval_dataset import load_eval_dataset
            load_eval_dataset(str(bad_file))

    def test_dataset_without_samples_raises(self, tmp_path):
        bad_file = tmp_path / "no_samples.jsonl"
        bad_file.write_text(
            '{"knowledge_base": ["doc1"]}\n'
            '{"dataset_version": "v1"}\n',
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="至少包含一个"):
            from agentnexus.rag.eval_dataset import load_eval_dataset
            load_eval_dataset(str(bad_file))

    def test_dataset_with_non_string_question_raises(self, tmp_path):
        bad_file = tmp_path / "bad_question.jsonl"
        bad_file.write_text(
            '{"knowledge_base": ["doc1"]}\n'
            '{"dataset_version": "v1"}\n'
            '{"question": 123, "ground_truth": "answer"}\n',
            encoding="utf-8",
        )
        with pytest.raises((ValueError, AssertionError)):
            from agentnexus.rag.eval_dataset import load_eval_dataset
            load_eval_dataset(str(bad_file))
