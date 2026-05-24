"""Tests for agentnexus.rag.evaluator — helpers and deterministic methods."""

from unittest.mock import MagicMock, patch

from agentnexus.rag.evaluator import (
    EvalRun,
    EvalSample,
    RAGEvaluator,
    _bootstrap_ci,
    _fit_token_budget,
    _is_refusal,
    _parse_score,
    _percentile,
    _safe_mean,
)
from agentnexus.rag.ingestion import ChunkStrategy


class TestParseScore:
    def test_none_returns_zero(self):
        assert _parse_score(None) == 0.0

    def test_empty_returns_zero(self):
        assert _parse_score("") == 0.0

    def test_extracts_first_number(self):
        assert _parse_score("score: 0.85") == 0.85

    def test_clamps_above_one(self):
        assert _parse_score("1.5") == 1.0

    def test_integer_works(self):
        assert _parse_score("5") == 1.0


class TestPercentile:
    def test_p50_uses_floor_index(self):
        assert _percentile([1.0, 2.0, 3.0, 4.0, 5.0], 50) == 2.0

    def test_p95_near_end(self):
        assert _percentile([1.0, 2.0, 3.0, 4.0, 10.0], 95) == 4.0

    def test_single_element(self):
        assert _percentile([42.0], 50) == 42.0


class TestSafeMean:
    def test_normal(self):
        assert _safe_mean([1.0, 2.0, 3.0]) == 2.0

    def test_empty_returns_zero(self):
        assert _safe_mean([]) == 0.0

    def test_single_value(self):
        assert _safe_mean([5.0]) == 5.0


class TestBootstrapCI:
    def test_fewer_than_3_returns_mean(self):
        lo, hi = _bootstrap_ci([0.5])
        assert lo == hi == 0.5

    def test_enough_scores_returns_range(self):
        scores = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]
        lo, hi = _bootstrap_ci(scores, n_resamples=100)
        assert lo <= hi
        assert 0.0 <= lo <= 1.0
        assert 0.0 <= hi <= 1.0

    def test_deterministic_seed(self):
        scores = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]
        a = _bootstrap_ci(scores, n_resamples=100)
        b = _bootstrap_ci(scores, n_resamples=100)
        assert a == b


class TestFitTokenBudget:
    def test_empty_chunks_returns_empty(self):
        assert _fit_token_budget([], 100) == []

    def test_fits_all_chunks(self):
        chunks = ["short", "tiny"]
        result = _fit_token_budget(chunks, 1000)
        assert result == ["short", "tiny"]

    def test_truncates_at_budget(self):
        chunks = ["a" * 100, "b" * 100, "c" * 100]
        result = _fit_token_budget(chunks, 30)
        assert len(result) == 1

    def test_first_chunk_always_included(self):
        chunks = ["a" * 200, "b" * 10]
        result = _fit_token_budget(chunks, 10)
        assert len(result) == 1


class TestIsRefusal:
    def test_empty_answer(self):
        assert _is_refusal("") is True

    def test_very_short_answer(self):
        assert _is_refusal("嗯") is True

    def test_long_enough_answer_without_keywords(self):
        assert _is_refusal("这是一个正常的回答内容。") is False

    def test_contains_keyword(self):
        assert _is_refusal("无法回答这个问题") is True
        assert _is_refusal("I don't know the answer") is True
        assert _is_refusal("没有找到相关信息") is True
        assert _is_refusal("cannot process request") is True

    def test_case_insensitive_matching(self):
        assert _is_refusal("NOT FOUND in database") is True


class TestRAGEvaluatorScoreContextRelevancy:
    def test_empty_retrieved_returns_zero(self):
        evaluator = RAGEvaluator([], [])
        assert evaluator._score_context_relevancy("query", []) == 0.0

    def test_empty_query_returns_zero(self):
        evaluator = RAGEvaluator([], [])
        assert evaluator._score_context_relevancy("", ["chunk"]) == 0.0

    def test_full_match(self):
        evaluator = RAGEvaluator([], [])
        score = evaluator._score_context_relevancy("hello world", ["hello world chunk"])
        assert score > 0.0

    def test_no_match(self):
        evaluator = RAGEvaluator([], [])
        score = evaluator._score_context_relevancy("abc", ["xyz"])
        assert score == 0.0

    def test_partial_match(self):
        evaluator = RAGEvaluator([], [])
        score = evaluator._score_context_relevancy("hello world foo", ["hello bar"])
        assert 0.0 < score < 1.0


class TestRAGEvaluatorScoreKeyword:
    def test_precision_empty_retrieved(self):
        evaluator = RAGEvaluator([], [])
        sample = EvalSample(question="q", ground_truth="gt", reference_contexts=["ref"])
        assert evaluator._score_precision_keyword(sample, []) == 0.0

    def test_precision_empty_reference(self):
        evaluator = RAGEvaluator([], [])
        sample = EvalSample(question="q", ground_truth="gt", reference_contexts=[])
        assert evaluator._score_precision_keyword(sample, ["chunk"]) == 0.0

    def test_precision_some_hits(self):
        evaluator = RAGEvaluator([], [])
        sample = EvalSample(question="q", ground_truth="gt", reference_contexts=["ref"])
        assert evaluator._score_precision_keyword(sample, ["ref chunk", "other"]) == 0.5

    def test_recall_empty_retrieved(self):
        evaluator = RAGEvaluator([], [])
        sample = EvalSample(question="q", ground_truth="gt", reference_contexts=["ref"])
        assert evaluator._score_recall_keyword(sample, []) == 0.0

    def test_recall_empty_reference(self):
        evaluator = RAGEvaluator([], [])
        sample = EvalSample(question="q", ground_truth="gt", reference_contexts=[])
        assert evaluator._score_recall_keyword(sample, ["chunk"]) == 0.0

    def test_recall_some_hits(self):
        evaluator = RAGEvaluator([], [])
        sample = EvalSample(question="q", ground_truth="gt", reference_contexts=["ref"])
        assert evaluator._score_recall_keyword(sample, ["ref chunk"]) == 1.0


class TestRAGEvaluatorChunkAll:
    def test_returns_chunks(self):
        evaluator = RAGEvaluator(["doc one", "doc two"], [])
        from agentnexus.rag.chunking import ChunkStrategy
        chunks = evaluator._chunk_all(ChunkStrategy.FIXED, 500, 0)
        assert len(chunks) >= 1

    def test_empty_chunks_fallback_to_docs(self):
        evaluator = RAGEvaluator(["  ", ""], [])
        from agentnexus.rag.chunking import ChunkStrategy
        chunks = evaluator._chunk_all(ChunkStrategy.FIXED, 100, 0)
        assert chunks == ["  ", ""]


class TestCheckPassed:
    def test_all_above_threshold_passes(self):
        run = EvalRun(label="t", strategy=ChunkStrategy.FIXED, chunk_size=256, use_hybrid=False)
        run.faithfulness = 0.90
        run.answer_relevancy = 0.85
        run.answer_correctness = 0.80
        run.hit_rate = 0.90
        run.mrr = 0.80
        run.rejection_rate = 0.80
        assert run.check_passed() is True

    def test_below_threshold_fails(self):
        run = EvalRun(label="t", strategy=ChunkStrategy.FIXED, chunk_size=256, use_hybrid=False)
        run.faithfulness = 0.50
        run.answer_relevancy = 0.85
        run.answer_correctness = 0.80
        run.hit_rate = 0.90
        run.mrr = 0.80
        run.rejection_rate = 0.80
        assert run.check_passed() is False

    def test_custom_threshold(self):
        run = EvalRun(label="t", strategy=ChunkStrategy.FIXED, chunk_size=256, use_hybrid=False)
        run.hit_rate = 0.95
        custom = {"hit_rate": 0.99, "faithfulness": 0.0, "answer_relevancy": 0.0,
                   "answer_correctness": 0.0, "mrr": 0.0, "rejection_rate": 0.0}
        assert run.check_passed(custom) is False

    def test_zero_defaults_fail_safe(self):
        run = EvalRun(label="t", strategy=ChunkStrategy.FIXED, chunk_size=256, use_hybrid=False)
        # All defaults are 0.0, thresholds are all > 0, so should fail
        assert run.check_passed() is False


class TestEvalRunNewFields:
    def test_default_values(self):
        run = EvalRun(label="test", strategy=ChunkStrategy.FIXED, chunk_size=256, use_hybrid=False)
        assert run.hit_rate == 0.0
        assert run.mrr == 0.0
        assert run.answer_correctness == 0.0
        assert run.answer_relevancy == 0.0

    def test_confidence_interval_defaults(self):
        run = EvalRun(label="t", strategy=ChunkStrategy.FIXED, chunk_size=256, use_hybrid=False)
        assert run.hit_rate_ci == (0.0, 0.0)
        assert run.mrr_ci == (0.0, 0.0)
        assert run.answer_correctness_ci == (0.0, 0.0)
        assert run.answer_relevancy_ci == (0.0, 0.0)

    def test_assign_values(self):
        run = EvalRun(label="t", strategy=ChunkStrategy.FIXED, chunk_size=256, use_hybrid=False)
        run.hit_rate = 0.85
        run.mrr = 0.72
        run.answer_correctness = 0.80
        run.answer_relevancy = 0.90
        assert run.hit_rate == 0.85
        assert run.mrr == 0.72
        assert run.answer_correctness == 0.80
        assert run.answer_relevancy == 0.90


class TestScoreAnswerRelevancy:
    def test_empty_answer_returns_zero(self):
        evaluator = RAGEvaluator([], [])
        evaluator._judge_llm = MagicMock()
        score = evaluator._score_answer_relevancy("question", "")
        assert score == 0.0
        evaluator._judge_llm.think.assert_not_called()

    def test_calls_judge_llm(self):
        evaluator = RAGEvaluator([], [])
        evaluator._judge_llm = MagicMock()
        evaluator._judge_llm.think.return_value = "0.85"
        score = evaluator._score_answer_relevancy("test question", "test answer")
        assert score == 0.85
        evaluator._judge_llm.think.assert_called_once()


class TestScoreRetrievalRanked:
    def test_empty_full_ranked_returns_zeros(self):
        evaluator = RAGEvaluator([], [])
        sample = EvalSample(question="q", ground_truth="gt", reference_contexts=["ref"])
        precision, hit, mrr = evaluator._score_retrieval_ranked(sample, [], top_k=10)
        assert precision == 0.0
        assert hit == 0.0
        assert mrr == 0.0

    def test_all_relevant_returns_one(self):
        evaluator = RAGEvaluator([], [])
        evaluator._judge_llm = MagicMock()
        evaluator._judge_llm.think.return_value = "1.0"
        sample = EvalSample(question="q", ground_truth="gt", reference_contexts=["ref"])
        precision, hit, mrr = evaluator._score_retrieval_ranked(
            sample, ["chunk1", "chunk2"], top_k=2,
        )
        assert precision == 1.0
        assert hit == 1.0
        assert mrr == 1.0

    def test_none_relevant_returns_zeros(self):
        evaluator = RAGEvaluator([], [])
        evaluator._judge_llm = MagicMock()
        evaluator._judge_llm.think.return_value = "0.0"
        sample = EvalSample(question="q", ground_truth="gt", reference_contexts=["ref"])
        precision, hit, mrr = evaluator._score_retrieval_ranked(
            sample, ["chunk1", "chunk2"], top_k=2,
        )
        assert precision == 0.0
        assert hit == 0.0
        assert mrr == 0.0

    def test_first_relevant_at_rank_2(self):
        evaluator = RAGEvaluator([], [])
        evaluator._judge_llm = MagicMock()
        # chunk1 not relevant, chunk2 relevant
        evaluator._judge_llm.think.side_effect = ["0.0", "1.0"]
        sample = EvalSample(question="q", ground_truth="gt", reference_contexts=["ref"])
        precision, hit, mrr = evaluator._score_retrieval_ranked(
            sample, ["chunk1", "chunk2"], top_k=2,
        )
        assert precision == 0.5
        assert hit == 1.0
        assert mrr == 0.5  # 1/2

    def test_respects_top_k_limit(self):
        evaluator = RAGEvaluator([], [])
        evaluator._judge_llm = MagicMock()
        evaluator._judge_llm.think.return_value = "1.0"
        sample = EvalSample(question="q", ground_truth="gt", reference_contexts=["ref"])
        chunks = [f"chunk{i}" for i in range(10)]
        evaluator._score_retrieval_ranked(sample, chunks, top_k=5)
        # Should only call Judge for 5 chunks
        assert evaluator._judge_llm.think.call_count == 5


class TestScoreCorrectness:
    def test_calls_judge_llm(self):
        evaluator = RAGEvaluator([], [])
        evaluator._judge_llm = MagicMock()
        evaluator._judge_llm.think.return_value = "0.75"
        score = evaluator._score_correctness("q", "answer", "ground_truth")
        assert score == 0.75
        evaluator._judge_llm.think.assert_called_once()


class TestFaithfulnessUsesJudgeLLM:
    def test_faithfulness_calls_judge_not_agent(self):
        evaluator = RAGEvaluator([], [])
        evaluator._judge_llm = MagicMock()
        evaluator._judge_llm.think.return_value = "0.9"
        answer = "Based on the context, Qdrant uses HNSW."
        contexts = ["Qdrant is a high-performance vector database using HNSW"]
        score = evaluator._score_faithfulness(answer, contexts)
        assert score == 0.9
        evaluator._judge_llm.think.assert_called_once()


class TestRetrieveReturnsTuple:
    def test_retrieve_returns_two_elements(self, monkeypatch):
        evaluator = RAGEvaluator(["doc content"], [])
        mock_retriever = MagicMock()
        mock_retriever.search.return_value = []
        with patch("agentnexus.rag.evaluator.search") as mock_search:
            mock_search.return_value = []
            result = evaluator._retrieve("query", mock_retriever, use_hybrid=False, max_tokens=100)
            assert isinstance(result, tuple)
            assert len(result) == 2
            assert isinstance(result[0], list)
            assert isinstance(result[1], list)
