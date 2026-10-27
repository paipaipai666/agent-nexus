"""Unit test conftest — mock embedding model to avoid HuggingFace downloads."""

from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _mock_embedding_model(monkeypatch):
    """Prevent all unit tests from downloading the SentenceTransformer model."""
    mock_model = MagicMock()
    mock_model.encode.side_effect = lambda texts, **_: (
        [0.1, 0.2, 0.3] if isinstance(texts, str) else [[0.1, 0.2, 0.3]] * len(texts)
    )

    def _mock_embed(texts):
        return [[0.1, 0.2, 0.3]] * len(texts)

    monkeypatch.setattr("agentnexus.rag.embeddings.get_embedding_model", lambda: mock_model)
    monkeypatch.setattr("agentnexus.rag.embeddings.embed_texts", _mock_embed)
    monkeypatch.setattr("agentnexus.rag.model_assets.prepare_embedding", lambda **_: (mock_model, "unit-test-embedding"))
