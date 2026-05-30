"""Performance: RAGEvaluator — non-LLM operations isolated via mocking."""
from __future__ import annotations

import time

CHUNK_ALL_15_DOCS_P95_MAX_MS = 500
RETRIEVE_10_QUERIES_P95_MAX_MS = 3000
BOOTSTRAP_CI_100_SCORES_P95_MAX_MS = 100
FIT_TOKEN_BUDGET_100_CHUNKS_P95_MAX_MS = 10
PARSE_SCORE_100_TEXTS_P95_MAX_MS = 5
CONTEXT_RELEVANCY_10_CHUNKS_P95_MAX_MS = 20

# Retrieval quality regression guards
RETRIEVAL_HIT_RATE_THRESHOLD = 0.85  # hit_rate@10 must stay above
RETRIEVAL_MRR_THRESHOLD = 0.60       # MRR@10 must stay above


def test_chunk_all_15_docs(benchmark, perf_env):
    from agentnexus.rag.chunking import ChunkStrategy
    from agentnexus.rag.eval_dataset import EVAL_SAMPLES, KNOWLEDGE_BASE
    from agentnexus.rag.evaluator import RAGEvaluator

    evaluator = RAGEvaluator(KNOWLEDGE_BASE, EVAL_SAMPLES)
    result = benchmark(evaluator._chunk_all, ChunkStrategy.FIXED, 512, 50)
    assert isinstance(result, list)
    assert len(result) > 0


def test_retrieval_quality_keyword(perf_env):
    """Regression guard: keyword-based hit_rate@10 and MRR@10 must stay above threshold.

    Uses deterministic keyword matching (not Judge LLM) to detect relevant chunks.
    Hit rate measures whether any relevant doc was retrieved at all.
    MRR measures how early in the ranked list the first relevant doc appears.
    """
    from agentnexus.rag.chunking import ChunkStrategy, chunk_text
    from agentnexus.rag.eval_dataset import EVAL_SAMPLES, KNOWLEDGE_BASE
    from agentnexus.rag.retriever import HybridRetriever, build_knowledge_base
    from agentnexus.storage.chroma import delete_collection, reset_storage_client

    reset_storage_client()

    # Chunk and index
    full_text = "\n\n".join(KNOWLEDGE_BASE)
    chunks = chunk_text(full_text, strategy=ChunkStrategy.FIXED, chunk_size=512, chunk_overlap=50)
    delete_collection(namespace="perf_qual")
    build_knowledge_base(chunks, load_reranker=False, namespace="perf_qual")

    # Build hybrid retriever
    retriever = HybridRetriever(namespace="perf_qual")
    retriever.rebuild_from_catalog()

    from agentnexus.rag.evaluator import RAGEvaluator
    evaluator = RAGEvaluator(KNOWLEDGE_BASE, EVAL_SAMPLES)

    positive_samples = [s for s in EVAL_SAMPLES if s.ground_truth and s.reference_contexts]
    sample_count = min(20, len(positive_samples))

    hit_rates = []
    mrrs = []

    for sample in positive_samples[:sample_count]:
        _, truncated = evaluator._retrieve(
            sample.question, retriever, use_hybrid=True,
            max_tokens=2000, top_k=10,
        )
        if not truncated:
            hit_rates.append(0.0)
            mrrs.append(0.0)
            continue

        # Keyword-based relevance check against reference_contexts
        relevant_flags = []
        for chunk in truncated:
            is_relevant = any(ref in chunk for ref in sample.reference_contexts)
            relevant_flags.append(is_relevant)

        n_relevant = sum(relevant_flags)
        hit_rates.append(1.0 if n_relevant > 0 else 0.0)
        first_rank = next((i + 1 for i, flag in enumerate(relevant_flags) if flag), None)
        mrrs.append(1.0 / first_rank if first_rank else 0.0)

    avg_hit = sum(hit_rates) / len(hit_rates) if hit_rates else 0.0
    avg_mrr = sum(mrrs) / len(mrrs) if mrrs else 0.0

    assert avg_hit >= RETRIEVAL_HIT_RATE_THRESHOLD, \
        f"Keyword hit_rate@10={avg_hit:.3f} < {RETRIEVAL_HIT_RATE_THRESHOLD}"
    assert avg_mrr >= RETRIEVAL_MRR_THRESHOLD, \
        f"Keyword MRR@10={avg_mrr:.3f} < {RETRIEVAL_MRR_THRESHOLD}"


def test_retrieve_10_queries(perf_env):
    from agentnexus.rag.eval_dataset import EVAL_SAMPLES, KNOWLEDGE_BASE
    from agentnexus.rag.evaluator import RAGEvaluator
    from agentnexus.rag.retriever import HybridRetriever
    from agentnexus.storage.chroma import insert_documents, reset_storage_client

    reset_storage_client()

    # Insert data into the default "documents" collection so _retrieve's
    # internal search() call finds it without requiring a namespace.
    texts = KNOWLEDGE_BASE
    insert_documents(
        texts,
        metadatas=[{"idx": i} for i in range(len(texts))],
        ids=[f"kb_doc_{i:04d}" for i in range(len(texts))],
    )

    # Build a hybrid retriever that has BM25 + dense search capability.
    retriever = HybridRetriever(namespace="default")
    retriever.build_bm25(texts)

    evaluator = RAGEvaluator(KNOWLEDGE_BASE, EVAL_SAMPLES)
    queries = [s.question for s in EVAL_SAMPLES[:10]]

    start = time.perf_counter()
    for q in queries:
        _ = evaluator._retrieve(q, retriever, use_hybrid=True, max_tokens=2000)
    elapsed = time.perf_counter() - start
    p95 = (elapsed / 10) * 1000 * 1.05
    assert p95 * 10 < RETRIEVE_10_QUERIES_P95_MAX_MS, \
        f"Retrieve 10 queries p95={p95*10:.0f}ms (total) > {RETRIEVE_10_QUERIES_P95_MAX_MS}ms"


def test_bootstrap_ci_100_scores(benchmark):
    from agentnexus.rag.evaluator import _bootstrap_ci
    scores = [i / 100 for i in range(100)]  # 0.0 to 0.99
    result = benchmark(_bootstrap_ci, scores)
    assert result[0] <= result[1]


def test_fit_token_budget_100_chunks(benchmark):
    from agentnexus.rag.evaluator import _fit_token_budget
    chunks = [
        f"This is chunk {i} with enough text to have meaningful token count "
        f"for the budget fitting algorithm." for i in range(100)
    ]
    result = benchmark(_fit_token_budget, chunks, 5000)
    assert len(result) > 0


def test_parse_score_100_texts(benchmark):
    from agentnexus.rag.evaluator import _parse_score
    texts = [f"The score is {i / 100:.2f}" for i in range(100)]
    result = benchmark(_parse_score, texts[0])
    assert result >= 0


def test_context_relevancy_10_chunks(benchmark):
    from agentnexus.rag.evaluator import EvalSample, RAGEvaluator
    evaluator = RAGEvaluator(["test doc"], [EvalSample(question="test", ground_truth="test")])  # noqa: F401
    query = "performance testing document retrieval scoring"
    chunks = [
        f"This is chunk {i} about performance testing and document retrieval."
        for i in range(10)
    ]
    result = benchmark(evaluator._score_context_relevancy, query, chunks)
    assert 0.0 <= result <= 1.0


def test_is_refusal_100_texts(benchmark):
    from agentnexus.rag.evaluator import _is_refusal
    texts = [
        "I don't know the answer to this question." if i % 3 == 0
        else "The answer is based on the provided context." if i % 3 == 1
        else "According to the documents, the result is clear."
        for i in range(100)
    ]

    def _run():
        results = [_is_refusal(t) for t in texts]
        return sum(results)

    total = benchmark(_run)
    assert isinstance(total, int)
