import json

import pytest

from agentnexus.rag.eval_dataset import DATASET_VERSION, EVAL_SAMPLES, KNOWLEDGE_BASE, load_eval_dataset


class TestKnowledgeBase:
    def test_length(self):
        assert len(KNOWLEDGE_BASE) == 15

    def test_all_non_empty(self):
        assert all(isinstance(doc, str) and doc.strip() for doc in KNOWLEDGE_BASE)

    def test_core_count(self):
        core = KNOWLEDGE_BASE[:12]
        assert len(core) == 12


class TestEvalSamples:
    def test_total_count(self):
        assert len(EVAL_SAMPLES) == 60

    def test_negative_samples_have_empty_ground_truth(self):
        negatives = [s for s in EVAL_SAMPLES if not s.ground_truth]
        assert len(negatives) == 11
        for s in negatives:
            assert s.reference_contexts == []

    def test_positive_samples_have_content(self):
        positives = [s for s in EVAL_SAMPLES if s.ground_truth]
        assert len(positives) == 49
        for s in positives:
            assert s.ground_truth.strip()
            assert len(s.reference_contexts) > 0

    def test_each_sample_has_question(self):
        for sample in EVAL_SAMPLES:
            assert sample.question.strip()

    def test_reference_contexts_snippet_in_knowledge_base(self):
        sample = EVAL_SAMPLES[0]
        kb_text = " ".join(KNOWLEDGE_BASE)
        for snippet in sample.reference_contexts:
            assert snippet in kb_text

    def test_dataset_version_defined(self):
        assert DATASET_VERSION == "built-in-v1"


class TestLoadEvalDataset:
    def test_loads_inline_knowledge_base(self, temp_agentnexus_home):
        dataset = temp_agentnexus_home / "inline.jsonl"
        rows = [
            {"dataset_version": "inline-v1"},
            {"knowledge_base": ["doc one", "doc two"]},
            {"question": "Q1", "ground_truth": "A1", "reference_contexts": ["doc one"]},
        ]
        dataset.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows), encoding="utf-8")

        kb, samples, version = load_eval_dataset(dataset)

        assert kb == ["doc one", "doc two"]
        assert len(samples) == 1
        assert version == "inline-v1"

    def test_loads_file_backed_knowledge_base(self, temp_agentnexus_home):
        doc = temp_agentnexus_home / "guide.md"
        doc.write_text("# Guide\n\nBody\n", encoding="utf-8")
        dataset = temp_agentnexus_home / "files.jsonl"
        rows = [
            {"dataset_version": "files-v1"},
            {"knowledge_base": [{"path": "guide.md"}]},
            {"question": "Q1", "ground_truth": "A1", "reference_contexts": ["Guide"]},
        ]
        dataset.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows), encoding="utf-8")

        kb, samples, version = load_eval_dataset(dataset)

        assert kb == [str(doc.resolve())]
        assert samples[0].question == "Q1"
        assert version == "files-v1"

    def test_rejects_missing_samples(self, temp_agentnexus_home):
        dataset = temp_agentnexus_home / "empty.jsonl"
        dataset.write_text(json.dumps({"knowledge_base": ["doc one"]}, ensure_ascii=False), encoding="utf-8")

        with pytest.raises(ValueError, match="至少包含一个评估样本"):
            load_eval_dataset(dataset)

    def test_rejects_invalid_reference_contexts(self, temp_agentnexus_home):
        dataset = temp_agentnexus_home / "bad.jsonl"
        rows = [
            {"knowledge_base": ["doc one"]},
            {"question": "Q1", "ground_truth": "A1", "reference_contexts": "not-a-list"},
        ]
        dataset.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows), encoding="utf-8")

        with pytest.raises(ValueError, match="reference_contexts"):
            load_eval_dataset(dataset)
