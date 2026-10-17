"""Text memory queries are encoded before reaching the vector store."""

from unittest.mock import MagicMock, patch


def test_http_and_terminal_search_use_query_embeddings():
    from agentnexus.services.memories import search_memories

    store = MagicMock()
    store.search.return_value = [{"id": 7, "content": "known fact"}]
    model = MagicMock()
    model.encode.return_value = [0.25, 0.75]
    with patch("agentnexus.rag.embeddings.get_embedding_model", return_value=model):
        assert search_memories(store, "known", 3) == [{"id": 7, "content": "known fact"}]
    store.search.assert_called_once_with(query_embedding=[0.25, 0.75], limit=3)
