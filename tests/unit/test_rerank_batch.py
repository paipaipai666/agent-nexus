"""Rerank batching: fixed batch size caps predict memory regardless of candidates."""

from __future__ import annotations

from agentnexus.rag import retriever as retriever_mod
from agentnexus.rag.retriever import _predict_in_batches


class _FakeReranker:
    def __init__(self):
        self.batch_sizes: list[int] = []

    def predict(self, pairs):
        self.batch_sizes.append(len(pairs))
        return [0.5] * len(pairs)


def test_predict_batches_capped():
    fake = _FakeReranker()
    pairs = [("q", f"doc {i}") for i in range(70)]
    scores = _predict_in_batches(fake, pairs)

    assert scores == [0.5] * 70
    assert fake.batch_sizes == [16, 16, 16, 16, 6]


def test_batch_size_constant_is_small():
    assert retriever_mod._RERANK_BATCH_SIZE <= 32
