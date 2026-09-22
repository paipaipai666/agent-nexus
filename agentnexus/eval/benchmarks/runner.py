"""Benchmark runner — document-level indexing and retrieval, zero LLM calls.

Two retrieval modes over the same index:

- ``dense``: single dense retriever over Chroma (embedding model = local
  sentence-transformers). This is the BEIR leaderboard-comparable pipeline.
- ``hybrid``: RRF(dense, BM25) with the project's own tokenizer/ranking
  primitives. Reported alongside ``dense``; never folded into it.

Document ids flow through unchanged (Chroma ids == benchmark doc ids), so
qrels matching is exact — no text-overlap guessing like the EvalSample track.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from pathlib import Path

from agentnexus.rag import ranking as _ranking
from agentnexus.rag.models import ChunkRecord

from . import metrics as _metrics
from .schema import DENSE, HYBRID, RETRIEVAL_MODES, BenchmarkData, DatasetResult

logger = logging.getLogger(__name__)

# Embedding is computed inside chroma.upsert_documents / chroma.search; both
# route through agentnexus.rag.embeddings. Tests inject a fake encoder via
# this seam instead of loading sentence-transformers.
EmbeddingEncoder = Callable[[list[str]], list[list[float]]]


class BenchmarkIndex:
    """In-memory BM25 + Chroma-backed dense index over benchmark documents."""

    def __init__(self, namespace: str):
        self.namespace = namespace
        self._bm25 = _ranking.BM25Index()
        self._bm25_source: list[ChunkRecord] | None = None
        self._chroma_search = None
        self._chroma_upsert = None
        self._texts: dict[str, str] = {}

    def contexts_for(self, doc_ids: list[str]) -> list[str]:
        """Return indexed text for ranked doc ids (e2e prompt assembly)."""
        return [self._texts[doc_id] for doc_id in doc_ids if doc_id in self._texts]

    def _lazy_chroma(self):
        if self._chroma_search is None:
            from agentnexus.storage.chroma import search as chroma_search
            from agentnexus.storage.chroma import upsert_documents as chroma_upsert

            self._chroma_search = chroma_search
            self._chroma_upsert = chroma_upsert
        return self._chroma_search, self._chroma_upsert

    def build(self, data: BenchmarkData, batch_size: int = 256) -> None:
        """Index every document as one unit (document-level, BEIR protocol)."""
        _, chroma_upsert = self._lazy_chroma()

        # Rebuild from a clean slate: a collection left behind by an interrupted
        # run may carry a different embedding dimension (e.g. after switching
        # models), which would fail the first upsert.
        self.clear()
        self._texts = {doc.doc_id: doc.indexed_text for doc in data.docs}

        self._bm25_source = [
            ChunkRecord(
                chunk_id=doc.doc_id,
                kb_id=self.namespace,
                document_id=doc.doc_id,
                document_version=doc.doc_id,
                chunk_index=0,
                text=doc.indexed_text,
                metadata={"source_uri": f"benchmark://{data.dataset}/{doc.doc_id}", "title": doc.title},
            )
            for doc in data.docs
        ]

        texts = [doc.indexed_text for doc in data.docs]
        ids = [doc.doc_id for doc in data.docs]
        metadatas = [{"source_uri": f"benchmark://{data.dataset}/{doc.doc_id}", "title": doc.title} for doc in data.docs]
        for start in range(0, len(texts), batch_size):
            chroma_upsert(
                texts[start : start + batch_size],
                metadatas=metadatas[start : start + batch_size],
                ids=ids[start : start + batch_size],
                namespace=self.namespace,
            )

    def clear(self) -> None:
        from agentnexus.storage.chroma import delete_collection

        delete_collection(namespace=self.namespace)

    def _dense_batch(self, queries: list[str], depth: int) -> list[list[str]]:
        """Batch-encode queries and batch-query Chroma (single lock round-trips)."""
        from agentnexus.rag import embeddings as embedding_service
        from agentnexus.storage.chroma import chroma_operation_lock, get_collection

        if not queries:
            return []
        model = embedding_service.get_embedding_model()
        encoded = model.encode(queries, normalize_embeddings=True)
        query_vecs = embedding_service.embedding_to_list(encoded)

        collection = get_collection(namespace=self.namespace)
        ranked: list[list[str]] = []
        step = 64
        with chroma_operation_lock():
            for start in range(0, len(queries), step):
                batch = query_vecs[start : start + step]
                result = collection.query(
                    query_embeddings=batch,
                    n_results=depth,
                    include=[],
                )
                ranked.extend(row or [] for row in result["ids"])
        return ranked

    def retrieve_all(self, queries: list[str], mode: str, depth: int) -> list[list[str]]:
        """Return ranked doc_ids per query; order matches the input query list."""
        dense_ranked = self._dense_batch(queries, depth)
        if mode == DENSE:
            return dense_ranked

        if mode == HYBRID:
            if self._bm25_source is not None:
                self._bm25.build(self._bm25_source)
                self._bm25_source = None  # release tokenized corpus memory
            fused_ranked: list[list[str]] = []
            for query, dense_ranking in zip(queries, dense_ranked, strict=True):
                dense_scores = [(doc_id, 1.0 / (60 + rank + 1)) for rank, doc_id in enumerate(dense_ranking)]
                sparse_scores = self._bm25.search(query, top_k=depth)
                fused = _ranking.reciprocal_rank_fusion(dense_scores, sparse_scores, k=60)
                fused_ranked.append(
                    [doc_id for doc_id, _ in sorted(fused.items(), key=lambda kv: kv[1], reverse=True)]
                )
            return fused_ranked

        raise ValueError(f"Unknown retrieval mode '{mode}'. Choose from {RETRIEVAL_MODES}")

    def retrieve(self, query: str, mode: str, depth: int) -> list[str]:
        """Single-query convenience wrapper over :meth:`retrieve_all`."""
        return self.retrieve_all([query], mode=mode, depth=depth)[0]


def run_retrieval(
    data: BenchmarkData,
    mode: str = DENSE,
    depth: int = 100,
    k: int = 10,
    namespace: str | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> DatasetResult:
    """Index, retrieve all queries, score. Pure local compute — no LLM."""
    if mode not in RETRIEVAL_MODES:
        raise ValueError(f"Unknown retrieval mode '{mode}'. Choose from {RETRIEVAL_MODES}")
    from agentnexus.core.config import get_settings

    namespace = namespace or f"benchmark-{data.suite}-{data.dataset}"
    embedding_model = get_settings().embedding_model

    # Benchmark corpora are one-shot evaluation artifacts: keep their ChromaDB
    # in a throwaway directory instead of the user's main knowledge-base store.
    # The main store's delete/recreate path is unreliable under load (soft-deleted
    # collections, compaction races), and a benchmark run must never touch it.
    import shutil
    import tempfile

    from agentnexus.storage.chroma import reset_storage_client

    settings = get_settings()
    original_persist_dir = settings.chroma_persist_dir
    tmp_dir = Path(tempfile.mkdtemp(prefix="agentnexus-benchmark-"))
    settings.chroma_persist_dir = str(tmp_dir)

    started = time.perf_counter()
    try:
        reset_storage_client()
        index = BenchmarkIndex(namespace=namespace)
        index.build(data)
        dense_ranked = index.retrieve_all([query.text for query in data.queries], mode=mode, depth=depth)
        rankings = {query.query_id: ranking for query, ranking in zip(data.queries, dense_ranked, strict=True)}
        if progress:
            progress(len(data.queries), len(data.queries))
        metric_map, n_with_rels = _metrics.score_rankings(rankings, data.qrels, k=k, recall_ks=(100,))
    finally:
        settings.chroma_persist_dir = original_persist_dir
        reset_storage_client()
        shutil.rmtree(tmp_dir, ignore_errors=True)

    return DatasetResult(
        suite=data.suite,
        dataset=data.dataset,
        mode=mode,
        embedding_model=embedding_model,
        n_docs=len(data.docs),
        n_queries=len(data.queries),
        n_queries_with_rels=n_with_rels,
        depth=depth,
        metrics=metric_map,
        elapsed_s=time.perf_counter() - started,
        license_note=data.license_note,
    )
