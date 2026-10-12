"""Unit test conftest — mock embedding model to avoid HuggingFace downloads."""

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _mock_embedding_model():
    """Prevent all unit tests from downloading the SentenceTransformer model."""
    mock_model = MagicMock()
    mock_model.encode.side_effect = lambda texts, **_: (
        [0.1, 0.2, 0.3] if isinstance(texts, str) else [[0.1, 0.2, 0.3]] * len(texts)
    )

    def _mock_embed(texts):
        return [[0.1, 0.2, 0.3]] * len(texts)

    with patch("agentnexus.rag.embeddings.get_embedding_model", return_value=mock_model):
        with patch("agentnexus.rag.embeddings.embed_texts", side_effect=_mock_embed):
            with patch("agentnexus.rag.model_assets.prepare_embedding", return_value=(mock_model, "unit-test-embedding")):
                yield
