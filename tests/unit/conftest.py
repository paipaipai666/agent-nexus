"""Unit test conftest — mock embedding model to avoid HuggingFace downloads."""

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _mock_embedding_model():
    """Prevent all unit tests from downloading the SentenceTransformer model."""
    mock_model = MagicMock()
    mock_model.encode.return_value = [[0.1, 0.2, 0.3]]

    def _mock_embed(texts):
        return [[0.1, 0.2, 0.3]] * len(texts)

    with patch("agentnexus.rag.embeddings.get_embedding_model", return_value=mock_model):
        with patch("agentnexus.rag.embeddings.embed_texts", side_effect=_mock_embed):
            with patch("agentnexus.codegraph.embeddings.embed_texts", side_effect=_mock_embed):
                yield
