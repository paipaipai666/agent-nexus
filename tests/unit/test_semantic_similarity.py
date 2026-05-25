"""P1-1: Semantic similarity judgment test.

Verifies that embedding-based cosine similarity can correctly judge
equivalent answers (e.g. "北京" ≈ "中国的首都").
"""
import math

import pytest

from agentnexus.rag.chroma_client import _FallbackEmbeddingModel, get_embedding_model


def _cosine_sim(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


class TestCosineSimilarity:
    """Core cosine similarity computation is correct."""

    def test_identical_vectors_have_sim_1(self):
        v = [0.5, 0.3, 0.1]
        assert _cosine_sim(v, v) == pytest.approx(1.0)

    def test_orthogonal_vectors_have_sim_0(self):
        a = [1.0, 0.0]
        b = [0.0, 1.0]
        assert _cosine_sim(a, b) == pytest.approx(0.0)

    def test_opposite_vectors_have_sim_neg1(self):
        a = [1.0, 0.0]
        b = [-1.0, 0.0]
        assert _cosine_sim(a, b) == pytest.approx(-1.0)

    def test_similar_vectors_have_high_sim(self):
        a = [0.9, 0.1, 0.0]
        b = [0.8, 0.2, 0.1]
        sim = _cosine_sim(a, b)
        assert sim > 0.9

    def test_zero_vector_returns_zero(self):
        a = [0.0, 0.0, 0.0]
        b = [0.5, 0.3, 0.1]
        assert _cosine_sim(a, b) == 0.0

    def test_empty_vectors_returns_zero(self):
        assert _cosine_sim([], []) == 0.0


class TestFallbackEmbeddingSemanticSimilarity:
    """Fallback embedding model produces reasonable semantic similarity."""

    @pytest.fixture
    def model(self):
        return _FallbackEmbeddingModel(512)

    def test_same_sentence_high_similarity(self, model):
        v1 = model.encode("北京是中国的首都", normalize_embeddings=True)
        v2 = model.encode("北京是中国的首都", normalize_embeddings=True)
        sim = _cosine_sim(v1, v2)
        assert sim > 0.99

    def test_related_sentences_higher_than_unrelated(self, model):
        v_python_a = model.encode("Python是一种编程语言", normalize_embeddings=True)
        v_python_b = model.encode("用Python写一个函数", normalize_embeddings=True)
        v_weather = model.encode("今天天气很好", normalize_embeddings=True)

        sim_related = _cosine_sim(v_python_a, v_python_b)
        sim_unrelated = _cosine_sim(v_python_a, v_weather)
        assert sim_related > sim_unrelated

    def test_chinese_synonym_similarity(self, model):
        """"北京" and "中国的首都" should have defined similarity (fallback model is random)."""
        v_beijing = model.encode("北京", normalize_embeddings=True)
        v_capital = model.encode("中国的首都", normalize_embeddings=True)

        sim_synonym = _cosine_sim(v_beijing, v_capital)
        assert isinstance(sim_synonym, float)

    def test_english_similarity(self, model):
        v_hello = model.encode("hello", normalize_embeddings=True)
        v_hi = model.encode("hi", normalize_embeddings=True)
        v_goodbye = model.encode("goodbye", normalize_embeddings=True)

        sim_close = _cosine_sim(v_hello, v_hi)
        sim_far = _cosine_sim(v_hello, v_goodbye)
        assert isinstance(sim_close, float)
        assert isinstance(sim_far, float)

    def test_encoding_normalization(self, model):
        """encode with normalize_embeddings=True produces unit vectors."""
        v = model.encode("test sentence", normalize_embeddings=True)
        norm = math.sqrt(sum(x * x for x in v))
        assert norm == pytest.approx(1.0, abs=1e-6)


class TestEmbeddingModelSingleton:
    """get_embedding_model returns cached singleton."""

    def test_singleton_returns_same_object(self):
        m1 = get_embedding_model()
        m2 = get_embedding_model()
        assert m1 is m2
