import math

from agentnexus.rag.embeddings import (
    VECTOR_DIM,
    _fallback_tokenize,
    _FallbackEmbeddingModel,
    embedding_to_list,
    get_embedding_model,
    reset_embedding_model,
)


class TestFallbackEmbeddingModel:
    def test_encode_single_returns_list_of_floats(self):
        model = _FallbackEmbeddingModel()
        result = model.encode("hello world")

        assert isinstance(result, list)
        assert len(result) == VECTOR_DIM
        assert all(isinstance(v, float) for v in result)

    def test_encode_batch_returns_list_of_lists(self):
        model = _FallbackEmbeddingModel()
        result = model.encode(["hello", "world"])

        assert isinstance(result, list)
        assert len(result) == 2
        assert all(isinstance(v, list) for v in result)
        assert all(len(v) == VECTOR_DIM for v in result)

    def test_encode_deterministic(self):
        model = _FallbackEmbeddingModel()
        a = model.encode("same input text")
        b = model.encode("same input text")

        assert a == b

    def test_encode_normalized(self):
        model = _FallbackEmbeddingModel()
        result = model.encode("some text for normalization")

        norm = math.sqrt(sum(v * v for v in result))
        assert abs(norm - 1.0) < 1e-6

    def test_encode_non_normalized(self):
        model = _FallbackEmbeddingModel()
        result = model.encode("no normalization", normalize_embeddings=False)

        norm = math.sqrt(sum(v * v for v in result))
        assert norm > 1.0 or all(v == 0.0 for v in result)


class TestFallbackTokenize:
    def test_cjk_text_generates_bigrams_trigrams(self):
        tokens = _fallback_tokenize("你好世界")

        assert "你" in tokens
        assert "好" in tokens
        assert "你好" in tokens
        assert "世" in tokens
        assert "界" in tokens
        assert "世界" in tokens
        assert "你好世" in tokens
        assert "好世界" in tokens

    def test_latin_text_returns_tokens(self):
        tokens = _fallback_tokenize("hello world test")

        assert "hello" in tokens
        assert "world" in tokens
        assert "test" in tokens

    def test_empty_returns_empty_list(self):
        assert _fallback_tokenize("") == []
        assert _fallback_tokenize("   ") == []
        assert _fallback_tokenize(None) == []

    def test_mixed_cjk_and_latin(self):
        tokens = _fallback_tokenize("hello你好world")

        assert "hello" in tokens
        assert "你" in tokens
        assert "好" in tokens
        assert "你好" in tokens
        assert "world" in tokens

    def test_punctuation_only_returns_normalized(self):
        tokens = _fallback_tokenize("...!!!")
        assert len(tokens) >= 1


class TestGetEmbeddingModel:
    def test_returns_model(self):
        reset_embedding_model()
        model = get_embedding_model()
        assert model is not None
        assert hasattr(model, "encode")

    def test_reset_clears_cache(self):
        reset_embedding_model()
        m1 = get_embedding_model()
        reset_embedding_model()
        m2 = get_embedding_model()

        assert m1 is not m2


class TestEmbeddingToList:
    def test_with_list_input_returns_same(self):
        data = [1.0, 2.0, 3.0]
        result = embedding_to_list(data)
        assert result is data

    def test_with_numpy_array_returns_list(self):
        try:
            import numpy as np

            arr = np.array([1.0, 2.0, 3.0])
            result = embedding_to_list(arr)
            assert isinstance(result, list)
            assert result == [1.0, 2.0, 3.0]
        except ImportError:
            pass

    def test_with_tuple_returns_tuple(self):
        data = (1.0, 2.0)
        result = embedding_to_list(data)
        assert result is data
