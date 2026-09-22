"""End-to-end runner test with a deterministic fake embedding model.

Fake encoder: identical text -> identical vector (cosine 1.0); different
text -> pseudo-random unit vectors (cosine ~0). That is enough to verify the
full pipeline: document-level indexing, doc-id pass-through, dense and
hybrid retrieval, and exact qrels scoring — all without LLM calls.
"""

import hashlib
import math

import pytest

from agentnexus.eval.benchmarks import runner as benchmark_runner
from agentnexus.eval.benchmarks.schema import DENSE, HYBRID, BenchmarkData, BenchmarkDoc, BenchmarkQuery


class _FakeEmbeddingModel:
    dim = 64

    def encode(self, inputs, normalize_embeddings=True):
        single = isinstance(inputs, str)
        texts = [inputs] if single else list(inputs)
        vectors = [self._vec(text) for text in texts]
        return vectors[0] if single else vectors

    def _vec(self, text):
        digest = hashlib.sha256(text.strip().lower().encode("utf-8")).digest()
        vec = [(byte / 127.5) - 1.0 for byte in digest[: self.dim]]
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]


@pytest.fixture
def fake_embeddings(monkeypatch):
    model = _FakeEmbeddingModel()
    monkeypatch.setattr("agentnexus.rag.embeddings.get_embedding_model", lambda: model)
    monkeypatch.setattr("agentnexus.rag.embeddings.embed_texts", lambda texts: model.encode(list(texts)))
    return model


@pytest.fixture
def isolated_chroma(tmp_path, monkeypatch):
    from agentnexus.core.config import get_settings
    from agentnexus.storage.chroma import reset_storage_client

    real_settings = get_settings()
    monkeypatch.setattr(real_settings, "chroma_persist_dir", str(tmp_path / "chroma"))
    reset_storage_client()
    yield real_settings
    reset_storage_client()


def _dataset():
    docs = [
        BenchmarkDoc(doc_id="d1", title="Alpha Project Overview", text=""),
        BenchmarkDoc(doc_id="d2", title="Beta Setup Guide", text=""),
        BenchmarkDoc(doc_id="d3", title="Gamma Release Notes", text=""),
    ]
    queries = [
        BenchmarkQuery(query_id="q1", text="Alpha Project Overview"),
        BenchmarkQuery(query_id="q2", text="Gamma Release Notes"),
    ]
    qrels = {"q1": {"d1": 1}, "q2": {"d3": 1}}
    return BenchmarkData(suite="test", dataset="toy", docs=docs, queries=queries, qrels=qrels)


@pytest.mark.parametrize("mode", [DENSE, HYBRID])
def test_run_retrieval_perfect_rankings(fake_embeddings, isolated_chroma, mode):
    result = benchmark_runner.run_retrieval(_dataset(), mode=mode, depth=10, k=10, namespace="bench-test-toy")

    assert result.n_docs == 3
    assert result.n_queries == 2
    assert result.n_queries_with_rels == 2
    # exact-text queries must rank their relevant doc first under both modes
    assert result.metrics["ndcg@10"] == pytest.approx(1.0)
    assert result.metrics["map"] == pytest.approx(1.0)
    assert result.metrics["hits@10"] == pytest.approx(1.0)
    assert result.metrics["recall@100"] == pytest.approx(1.0)
    assert result.mode == mode
    assert result.embedding_model  # records whichever model settings reported
    assert result.elapsed_s >= 0.0


def test_unknown_mode_rejected(fake_embeddings, isolated_chroma):
    with pytest.raises(ValueError, match="retrieval mode"):
        benchmark_runner.run_retrieval(_dataset(), mode="bogus", namespace="bench-test-bogus")
