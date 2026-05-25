"""Performance regression: BM25 index cold-start rebuild.

AGENTS.md notes BM25 index is not persisted; every session restart
requires rebuilding from the knowledge base catalog. This test
benchmarks the rebuild cost and asserts it does not regress.

The rebuild cost is dominated by jieba tokenization. For CJK-heavy
corpora, this is a significant startup cost.
"""
from __future__ import annotations

import pytest

from agentnexus.rag.models import ChunkRecord
from agentnexus.rag.retriever import BM25Index


@pytest.mark.parametrize("chunk_records", [100], indirect=True)
def test_bm25_rebuild_small_cold_start(benchmark, chunk_records: list[ChunkRecord]):
    """Cold-start BM25 rebuild with 100 chunks — simulates session restart."""
    def _rebuild():
        idx = BM25Index()
        idx.build(chunk_records)
    benchmark(_rebuild)


@pytest.mark.parametrize("chunk_records", [500], indirect=True)
def test_bm25_rebuild_medium_cold_start(benchmark, chunk_records: list[ChunkRecord]):
    """Cold-start BM25 rebuild with 500 chunks."""
    def _rebuild():
        idx = BM25Index()
        idx.build(chunk_records)
    benchmark(_rebuild)


@pytest.mark.parametrize("chunk_records", [5000], indirect=True)
def test_bm25_rebuild_large_cold_start(benchmark, chunk_records: list[ChunkRecord]):
    """Cold-start BM25 rebuild with 5000 chunks — representative catalog size."""
    def _rebuild():
        idx = BM25Index()
        idx.build(chunk_records)
    benchmark.pedantic(_rebuild, rounds=5)
