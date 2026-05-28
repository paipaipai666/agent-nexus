"""Performance tests for RAG ranking — BM25 build/search, RRF fusion, tokenization."""

from __future__ import annotations

import pytest

from agentnexus.rag.ranking import BM25Index, reciprocal_rank_fusion, tokenize

# ── BM25Index.build() ─────────────────────────────────────────


class TestBM25Build:
    @pytest.mark.parametrize("chunk_records", [100, 500, 1000], indirect=True)
    def test_build_scaling(self, benchmark, chunk_records):
        index = BM25Index()

        def _build():
            index.build(chunk_records)

        benchmark(_build)
        assert len(index._chunk_ids) == len(chunk_records)


# ── BM25Index.search() ────────────────────────────────────────


class TestBM25Search:
    @pytest.mark.parametrize("chunk_records", [1000], indirect=True)
    def test_search_large_index(self, benchmark, chunk_records):
        index = BM25Index()
        index.build(chunk_records)

        result = benchmark(index.search, "retrieval testing content", top_k=10)
        assert isinstance(result, list)
        assert len(result) <= 10

    @pytest.mark.parametrize("top_k", [5, 20, 50])
    def test_search_top_k_scaling(self, benchmark, chunk_records, top_k):
        index = BM25Index()
        index.build(chunk_records)

        result = benchmark(index.search, "chunk content", top_k=top_k)
        assert isinstance(result, list)
        assert len(result) <= top_k


# ── reciprocal_rank_fusion ────────────────────────────────────


class TestReciprocalRankFusion:
    def test_fusion_100_results_each(self, benchmark):
        dense = [(f"dense_{i}", 1.0 - i * 0.01) for i in range(100)]
        sparse = [(f"sparse_{i}", 1.0 - i * 0.01) for i in range(100)]

        result = benchmark(reciprocal_rank_fusion, dense, sparse)
        assert isinstance(result, dict)
        assert len(result) > 0

    def test_fusion_with_overlap(self, benchmark):
        shared = [f"shared_{i}" for i in range(50)]
        dense = [(cid, 1.0 - i * 0.01) for i, cid in enumerate(shared)]
        sparse = [(cid, 1.0 - i * 0.02) for i, cid in enumerate(shared)]
        dense += [(f"dense_only_{i}", 0.5) for i in range(50)]
        sparse += [(f"sparse_only_{i}", 0.5) for i in range(50)]

        result = benchmark(reciprocal_rank_fusion, dense, sparse)
        assert len(result) == 150


# ── tokenize() ────────────────────────────────────────────────


class TestTokenize:
    def test_tokenize_long_text(self, benchmark):
        text = "这是一段用于分词性能测试的中文文本，包含多种标点符号和术语。" * 200

        result = benchmark(tokenize, text)
        assert isinstance(result, list)
        assert len(result) > 0

    def test_tokenize_short_text(self, benchmark):
        result = benchmark(tokenize, "简单的查询")
        assert isinstance(result, list)
