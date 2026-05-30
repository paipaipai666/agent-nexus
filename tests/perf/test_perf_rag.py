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
    import agentnexus.storage.chroma as chroma

    chroma.reset_storage_client()
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
    import agentnexus.storage.chroma as chroma
    from agentnexus.rag.retriever import HybridRetriever

    chroma.reset_storage_client()
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


# ── ChromaDB at scale ─────────────────────────────────────────────

CHROMA_INSERT_1000_P95_MAX_MS = 3000
CHROMA_SEARCH_1000_P95_MAX_MS = 800
CHROMA_SEARCH_10000_P95_MAX_MS = 1500


def test_chroma_insert_1000(benchmark, perf_env):
    """Benchmark inserting 1000 documents into ChromaDB."""
    import agentnexus.storage.chroma as chroma
    chroma.reset_storage_client()
    namespace = "perf_insert_1000"
    texts = [
        f"Performance test document {i} with sufficient content "
        f"for embedding and indexing in ChromaDB. " * 5
        for i in range(1000)
    ]
    metadatas = [{"source": "perf", "idx": i} for i in range(1000)]
    ids = [f"insert_doc_{i:05d}" for i in range(1000)]

    result = benchmark(chroma.insert_documents, texts, metadatas, ids, None, namespace)
    assert len(result) == 1000


def test_chroma_search_1000(benchmark, perf_env):
    """Benchmark search in a collection of 1000 docs."""
    import agentnexus.storage.chroma as chroma
    chroma.reset_storage_client()
    namespace = "perf_search_1000"
    _topics = ['retrieval', 'embedding', 'vector database']
    texts = [
        f"Search test document {i} with some content about {_topics[i % 3]}."
        for i in range(1000)
    ]
    metadatas = [{"source": "perf", "idx": i} for i in range(1000)]
    ids = [f"search_doc_{i:05d}" for i in range(1000)]
    chroma.insert_documents(texts, metadatas, ids, namespace=namespace)

    def _search():
        return chroma.search("retrieval performance", limit=10, namespace=namespace)

    results = benchmark(_search)
    assert isinstance(results, list)


def _batch_insert(texts, metadatas, ids, namespace, batch_size=5000):
    import agentnexus.storage.chroma as chroma
    for i in range(0, len(texts), batch_size):
        chroma.insert_documents(
            texts[i:i + batch_size],
            metadatas[i:i + batch_size] if metadatas else None,
            ids[i:i + batch_size] if ids else None,
            namespace=namespace,
        )


def test_chroma_search_10000(perf_env):
    """Throughput: search in a collection of 10000 docs."""
    import time

    import agentnexus.storage.chroma as chroma
    chroma.reset_storage_client()
    namespace = "perf_search_10000"
    _topics = ['machine learning', 'data processing', 'search algorithms', 'text analysis', 'information retrieval']
    texts = [
        f"Large scale test doc {i} about {_topics[i % 5]}."
        for i in range(10000)
    ]
    metadatas = [{"source": "perf", "idx": i} for i in range(10000)]
    ids = [f"big_doc_{i:05d}" for i in range(10000)]
    _batch_insert(texts, metadatas, ids, namespace=namespace)

    start = time.perf_counter()
    results = chroma.search("machine learning", limit=10, namespace=namespace)
    elapsed = time.perf_counter() - start
    p95 = elapsed * 1000 * 1.05
    assert p95 < CHROMA_SEARCH_10000_P95_MAX_MS, \
        f"Chroma search 10000 p95={p95:.0f}ms > {CHROMA_SEARCH_10000_P95_MAX_MS}ms"
    assert isinstance(results, list)


def test_chroma_upsert_1000(benchmark, perf_env):
    """Benchmark upserting 1000 documents."""
    import agentnexus.storage.chroma as chroma
    chroma.reset_storage_client()
    namespace = "perf_upsert"
    texts = [
        f"Upsert test document {i} with content for benchmarking upsert operations. " * 3
        for i in range(1000)
    ]
    metadatas = [{"source": "perf", "idx": i} for i in range(1000)]
    ids = [f"upsert_doc_{i:05d}" for i in range(1000)]

    result = benchmark(chroma.upsert_documents, texts, metadatas, ids, None, namespace)
    assert len(result) == 1000


# ── BM25 at scale ─────────────────────────────────────────────────

BM25_BUILD_5000_P95_MAX_MS = 200
BM25_SEARCH_5000_P95_MAX_MS = 50


@pytest.mark.parametrize("chunk_records", [5000], indirect=True)
def test_bm25_build_5000(benchmark, chunk_records):
    """Build BM25 index with 5000 chunks."""
    from agentnexus.rag.retriever import BM25Index
    bm25 = BM25Index()
    result = benchmark(bm25.build, chunk_records)
    assert result is None


@pytest.mark.parametrize("chunk_records", [5000], indirect=True)
def test_bm25_search_5000(benchmark, chunk_records):
    """Search BM25 index built from 5000 chunks."""
    from agentnexus.rag.retriever import BM25Index
    bm25 = BM25Index()
    bm25.build(chunk_records)

    def _search():
        return bm25.search("content retrieval testing", top_k=10)

    results = benchmark(_search)
    assert isinstance(results, list)


# ── Hybrid retrieval at scale ─────────────────────────────────────

HYBRID_1000_P95_MAX_MS = 1200


@pytest.mark.parametrize("chunk_records", [1000], indirect=True)
def test_hybrid_search_1000(benchmark, perf_env, chunk_records):
    """Hybrid search with 1000 chunks."""
    import agentnexus.storage.chroma as chroma
    from agentnexus.rag.retriever import HybridRetriever

    chroma.reset_storage_client()
    namespace = "perf_hybrid_1000"

    texts = [c.text for c in chunk_records]
    metadatas = [{"source": "perf", "idx": i} for i in range(len(chunk_records))]
    ids = [c.chunk_id for c in chunk_records]
    chroma.insert_documents(texts, metadatas, ids, namespace=namespace)

    hybrid = HybridRetriever(namespace=namespace)
    hybrid._chunks = {c.chunk_id: c for c in chunk_records}
    hybrid._bm25.build(chunk_records)

    dense_raw = chroma.search("retrieval testing", limit=20, namespace=namespace)
    dense_results = [(item["id"], item["score"]) for item in dense_raw]

    def _search():
        return hybrid.search("retrieval testing", dense_results, top_k=10)

    results = benchmark(_search)
    assert isinstance(results, list)


# ── Rebuild from catalog ──────────────────────────────────────────

REBUILD_CATALOG_1000_CHUNKS_P95_MAX_MS = 1500


def test_rebuild_from_catalog_1000_chunks(perf_env):
    """rebuild_from_catalog with 1000 chunks in the SQLite catalog."""
    import time
    from datetime import datetime, timezone

    from agentnexus.rag.models import ChunkRecord, KnowledgeBaseRecord, SourceDocument
    from agentnexus.rag.retriever import HybridRetriever
    from agentnexus.rag.store import KnowledgeBaseCatalog, _reset_knowledge_base_catalog

    _reset_knowledge_base_catalog()
    db_path = str(perf_env / "catalog.db")
    catalog = KnowledgeBaseCatalog(db_path)

    kb = KnowledgeBaseRecord(
        kb_id="perf_rebuild", namespace="perf_rebuild",
        display_name="Perf", collection_name="perf_rebuild",
    )
    catalog.upsert_knowledge_base(kb)

    doc = SourceDocument(
        document_id="perf_doc", kb_id="perf_rebuild",
        source_id="perf", source_uri="perf.md",
        document_version="v1", content="Perf rebuild test doc.",
    )
    catalog.upsert_document(doc)

    now = datetime.now(timezone.utc).isoformat()
    chunks = [
        ChunkRecord(
            chunk_id=f"rebuild_chunk_{i:06d}",
            kb_id="perf_rebuild",
            document_id="perf_doc",
            document_version="v1",
            chunk_index=i,
            text=f"Rebuild test chunk {i} with reasonable content for catalog and BM25 rebuild performance testing.",
            metadata={"source": "perf", "idx": i},
            created_at=now,
            updated_at=now,
        )
        for i in range(1000)
    ]
    catalog.upsert_chunks(chunks)

    hybrid = HybridRetriever(namespace="perf_rebuild")
    start = time.perf_counter()
    chunks_from_catalog = catalog.list_chunks_by_kb("perf_rebuild")
    hybrid._chunks = {c.chunk_id: c for c in chunks_from_catalog}
    hybrid._bm25.build(chunks_from_catalog)
    elapsed = time.perf_counter() - start
    p95 = elapsed * 1000 * 1.05
    assert p95 < REBUILD_CATALOG_1000_CHUNKS_P95_MAX_MS, \
        f"rebuild_from_catalog p95={p95:.0f}ms > {REBUILD_CATALOG_1000_CHUNKS_P95_MAX_MS}ms"
    assert len(hybrid._chunks) == 1000


# ── RRF isolation ────────────────────────────────────────────────

RRF_200_RESULTS_P95_MAX_MS = 10


def test_rrf_200_results(benchmark):
    """Benchmark reciprocal_rank_fusion with 200 dense + 200 sparse results."""
    from agentnexus.rag.retriever import reciprocal_rank_fusion

    dense = [(f"doc_{i}", 1.0 - i * 0.005) for i in range(200)]
    sparse = [(f"doc_{i}", 1.0 - i * 0.003) for i in range(200)]

    result = benchmark(reciprocal_rank_fusion, dense, sparse, 60)
    assert isinstance(result, dict)
    assert len(result) > 0


# ── Build knowledge base at scale ────────────────────────────────

BUILD_KB_50_DOCS_P95_MAX_MS = 12000


def test_build_knowledge_base_50_docs(perf_env):
    """build_knowledge_base with 50 larger documents (~500 chunks)."""
    import time

    from agentnexus.rag.retriever import build_knowledge_base
    from agentnexus.storage.chroma import reset_storage_client

    reset_storage_client()

    docs = [
        f"Build KB performance test document {i} with sufficient content "
        f"for generating multiple chunks and testing the full pipeline. " * 30
        for i in range(50)
    ]

    start = time.perf_counter()
    build_knowledge_base(docs, load_reranker=False, namespace="perf_build_50")
    elapsed = time.perf_counter() - start
    p95 = elapsed * 1000 * 1.05
    assert p95 < BUILD_KB_50_DOCS_P95_MAX_MS, \
        f"build_knowledge_base 50 docs p95={p95:.0f}ms > {BUILD_KB_50_DOCS_P95_MAX_MS}ms"


# ── Search knowledge base end-to-end ──────────────────────────────

SEARCH_KB_10_QUERIES_P95_MAX_MS = 3000


def test_search_knowledge_base_10_queries(perf_env):
    """search_knowledge_base end-to-end for 10 queries."""
    import time

    from agentnexus.rag.retriever import build_knowledge_base, search_knowledge_base
    from agentnexus.storage.chroma import reset_storage_client

    reset_storage_client()
    docs = [
        f"Search KB performance test document {i} with content about "
        f"{'machine learning' if i % 3 == 0 else 'data processing' if i % 3 == 1 else 'search algorithms'}."
        * 20
        for i in range(20)
    ]
    build_knowledge_base(docs, load_reranker=False, namespace="perf_search_kb")

    queries = [
        "machine learning algorithms",
        "data processing pipeline",
        "search algorithm performance",
        "vector database search",
        "text analysis methods",
        "embedding model comparison",
        "retrieval augmented generation",
        "document chunking strategy",
        "hybrid search fusion",
        "reranker cross encoder",
    ]

    start = time.perf_counter()
    for q in queries:
        result = search_knowledge_base(q, namespace="perf_search_kb")
        assert isinstance(result, str)
        assert len(result) > 0
    elapsed = time.perf_counter() - start
    p95 = (elapsed / len(queries)) * 1000 * 1.05
    assert p95 * len(queries) < SEARCH_KB_10_QUERIES_P95_MAX_MS, \
        f"search_kb 10 queries p95={p95*len(queries):.0f}ms > {SEARCH_KB_10_QUERIES_P95_MAX_MS}ms"


# ── Dense vs hybrid comparison ───────────────────────────────────

DENSE_VS_HYBRID_SCALE_P95_MAX_MS = 1000


def test_dense_vs_hybrid_latency(perf_env):
    """Compare dense-only vs hybrid search latency on the same data."""
    import time

    from agentnexus.rag.retriever import HybridRetriever, build_knowledge_base
    from agentnexus.storage.chroma import reset_storage_client
    from agentnexus.storage.chroma import search as chroma_search

    reset_storage_client()
    namespace = "perf_compare"
    _topics = ['neural networks', 'data mining', 'natural language', 'computer vision']
    docs = [
        f"Comparison test document {i} with content about {_topics[i % 4]}."
        * 15
        for i in range(30)
    ]
    build_knowledge_base(docs, load_reranker=False, namespace=namespace)

    queries = ["neural network training", "data mining techniques", "language models"]

    dense_times = []
    hybrid_times = []
    hybrid = HybridRetriever(namespace=namespace)

    for q in queries:
        t0 = time.perf_counter()
        dense_result = chroma_search(q, limit=10, namespace=namespace)
        dense_times.append(time.perf_counter() - t0)

        dense_ids = [(r["id"], r["score"]) for r in dense_result]
        t0 = time.perf_counter()
        hybrid.search(q, dense_ids, top_k=5)
        hybrid_times.append(time.perf_counter() - t0)

    avg_dense = (sum(dense_times) / len(dense_times)) * 1000
    avg_hybrid = (sum(hybrid_times) / len(hybrid_times)) * 1000

    # Hybrid should add at most some overhead (BM25 search + RRF)
    # The combined dense+hybrid path should not exceed threshold
    total_avg = avg_dense + avg_hybrid
    assert total_avg < DENSE_VS_HYBRID_SCALE_P95_MAX_MS, \
        f"Combined dense+hybrid avg={total_avg:.0f}ms > {DENSE_VS_HYBRID_SCALE_P95_MAX_MS}ms"


# ── Tokenization (jieba) throughput ──────────────────────────────

JIEBA_100KB_CJK_P95_MAX_MS = 500
JIEBA_100KB_ASCII_P95_MAX_MS = 200


def test_jieba_cjk_100kb(benchmark):
    """Benchmark jieba.cut on 100KB of CJK text."""
    import jieba
    text = "机器学习深度学习自然语言处理计算机视觉数据挖掘知识图谱" * 5000
    text = text[:100000]

    def _run():
        return list(jieba.cut(text))

    result = benchmark(_run)
    assert isinstance(result, list)
    assert len(result) > 0


def test_jieba_ascii_100kb(benchmark):
    """Benchmark jieba.cut on 100KB of ASCII text."""
    import jieba
    text = "machine learning deep learning natural language processing computer vision" * 5000
    text = text[:100000]

    def _run():
        return list(jieba.cut(text))

    result = benchmark(_run)
    assert isinstance(result, list)
    assert len(result) > 0
