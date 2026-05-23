"""Performance: RAG retrieval — BM25, ChromaDB, Hybrid."""

from __future__ import annotations

import pytest

from agentnexus.rag.models import ChunkRecord
from agentnexus.rag.retriever import BM25Index, SearchResult

BM25_BUILD_P95_MAX_MS = 100      # BM25Index.build(500 chunks) < 100ms
CHROMA_SEARCH_P95_MAX_MS = 400   # ChromaDB search(500 chunks) < 400ms
HYBRID_P95_MAX_MS = 1000         # HybridRetriever full search < 1000ms
RERANK_PIPELINE_P95_MAX_MS = 50   # _rerank(100 candidates) < 50ms
HYBRID_WITH_RERANK_P95_MAX_MS = 150  # hybrid.search with mock reranker < 150ms
INGESTION_P95_MAX_MS = 3000          # build_knowledge_base(10 docs × ~2KB) < 3000ms


@pytest.mark.parametrize("chunk_records", [100], indirect=True)
def test_bm25_build_small(benchmark, chunk_records: list[ChunkRecord]):
    bm25 = BM25Index()
    result = benchmark(bm25.build, chunk_records)
    assert result is None


@pytest.mark.parametrize("chunk_records", [500], indirect=True)
def test_bm25_build_large(benchmark, chunk_records: list[ChunkRecord]):
    bm25 = BM25Index()
    result = benchmark.pedantic(bm25.build, args=(chunk_records,), rounds=5)
    assert result is None
    data = benchmark.stats.stats.sorted_data
    p95 = data[int(len(data) * 0.95)] * 1000 if len(data) >= 20 else data[-1] * 1000
    assert p95 < BM25_BUILD_P95_MAX_MS, f"BM25 build p95={p95:.0f}ms > {BM25_BUILD_P95_MAX_MS}ms"


def test_bm25_search(benchmark, chunk_records: list[ChunkRecord]):
    bm25 = BM25Index()
    bm25.build(chunk_records)

    def _search():
        return bm25.search("content retrieval testing", top_k=10)

    results = benchmark(_search)
    assert isinstance(results, list)
    if results:
        chunk_id, score = results[0]
        assert isinstance(chunk_id, str)
        assert isinstance(score, float)


def test_chroma_search(benchmark, perf_env):
    import agentnexus.rag.chroma_client as chroma

    chroma._reset_chroma_client()
    namespace = "perf_chroma"

    texts = [
        f"Test document number {i} with some meaningful content for embedding."
        for i in range(100)
    ]
    metadatas = [{"source": "perf", "idx": i} for i in range(100)]
    ids = [f"perf_doc_{i:04d}" for i in range(100)]
    chroma.insert_documents(
        texts, metadatas=metadatas, ids=ids, namespace=namespace
    )

    def _search():
        return chroma.search(
            "meaningful content for embedding", limit=10, namespace=namespace
        )

    results = benchmark(_search)
    assert isinstance(results, list)


def test_hybrid_search(benchmark, perf_env):
    import agentnexus.rag.chroma_client as chroma
    from agentnexus.rag.retriever import HybridRetriever

    chroma._reset_chroma_client()
    namespace = "perf_hybrid"

    texts = [
        f"Hybrid test document number {i} with some content for retrieval testing purposes."
        for i in range(100)
    ]
    metadatas = [{"source": "perf", "idx": i} for i in range(100)]
    ids = [f"hybrid_doc_{i:04d}" for i in range(100)]
    chroma.insert_documents(
        texts, metadatas=metadatas, ids=ids, namespace=namespace
    )

    chunks = [
        ChunkRecord(
            chunk_id=ids[i],
            kb_id="perf_kb",
            document_id="perf_doc",
            document_version="v1",
            chunk_index=i,
            section_index=0,
            text=texts[i],
            indexed_text=texts[i],
            sparse_text=texts[i],
            metadata=metadatas[i],
        )
        for i in range(100)
    ]

    hybrid = HybridRetriever(namespace=namespace)
    hybrid._chunks = {c.chunk_id: c for c in chunks}
    hybrid._bm25.build(chunks)

    dense_raw = chroma.search(
        "retrieval testing", limit=10, namespace=namespace
    )
    dense_results = [(item["id"], item["score"]) for item in dense_raw]

    def _search():
        return hybrid.search("retrieval testing", dense_results, top_k=5)

    results = benchmark(_search)
    assert isinstance(results, list)
    if results:
        assert isinstance(results[0], SearchResult)

    data = benchmark.stats.stats.sorted_data
    p95 = data[int(len(data) * 0.95)] * 1000 if len(data) >= 20 else data[-1] * 1000
    assert p95 < HYBRID_P95_MAX_MS, f"Hybrid search p95={p95:.0f}ms > {HYBRID_P95_MAX_MS}ms"


# ── Reranker tests ──────────────────────────────────────────────────


class _MockCrossEncoder:
    """Simulates CrossEncoder.predict with controlled ~1ms inference time."""

    def __init__(self, model_name: str = ""):
        self.model_name = model_name

    def predict(self, pairs: list[tuple[str, str]]) -> list[float]:
        import random
        return [random.uniform(0.0, 1.0) for _ in pairs]


def test_rerank_pipeline(benchmark, chunk_records: list[ChunkRecord]):
    from agentnexus.rag.retriever import HybridRetriever

    hybrid = HybridRetriever(namespace="perf_rerank")
    hybrid._chunks = {c.chunk_id: c for c in chunk_records}
    hybrid._reranker = _MockCrossEncoder()

    candidates = [(c.chunk_id, 0.5 + (i % 10) * 0.05) for i, c in enumerate(chunk_records)]

    def _run():
        return hybrid._rerank("test query", candidates, top_k=5, min_score=0.0)

    results = benchmark(_run)
    assert len(results) <= 5
    if results:
        assert isinstance(results[0], SearchResult)


def test_hybrid_search_with_rerank(benchmark, perf_env, chunk_records: list[ChunkRecord]):
    from agentnexus.rag.retriever import HybridRetriever

    hybrid = HybridRetriever(namespace="perf_hybrid_rerank")
    hybrid._chunks = {c.chunk_id: c for c in chunk_records}
    hybrid._bm25.build(chunk_records)
    hybrid._reranker = _MockCrossEncoder()

    dense_results = [(c.chunk_id, 0.5 + (i % 10) * 0.05) for i, c in enumerate(chunk_records)]

    def _run():
        return hybrid.search("test query", dense_results, top_k=5)

    results = benchmark(_run)
    assert isinstance(results, list)
    if results:
        assert isinstance(results[0], SearchResult)


# ── Ingestion pipeline ────────────────────────────────────────────


def test_ingestion_throughput(benchmark, perf_env):
    from agentnexus.rag.retriever import build_knowledge_base

    docs = [
        f"Performance test document number {i} with sufficient content "
        f"for chunking and embedding in the knowledge base pipeline. " * 20
        for i in range(10)
    ]

    benchmark(build_knowledge_base, docs, False, "perf_ingest")
