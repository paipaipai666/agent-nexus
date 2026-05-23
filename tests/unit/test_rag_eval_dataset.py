from agentnexus.rag.eval_dataset import EVAL_SAMPLES, KNOWLEDGE_BASE


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
        assert len(EVAL_SAMPLES) == 30

    def test_negative_samples_have_empty_ground_truth(self):
        for sample in EVAL_SAMPLES[26:30]:
            assert sample.ground_truth == ""
            assert sample.reference_contexts == []

    def test_factual_samples_have_content(self):
        for sample in EVAL_SAMPLES[:13]:
            assert sample.ground_truth.strip()
            assert len(sample.reference_contexts) > 0

    def test_each_sample_has_question(self):
        for sample in EVAL_SAMPLES:
            assert sample.question.strip()

    def test_reference_contexts_snippet_in_knowledge_base(self):
        sample = EVAL_SAMPLES[0]
        kb_text = " ".join(KNOWLEDGE_BASE)
        for snippet in sample.reference_contexts:
            assert snippet in kb_text
