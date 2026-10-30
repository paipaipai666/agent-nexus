"""Memory queries use the selected backend's public text interface."""

from unittest.mock import MagicMock, patch


def test_http_and_terminal_search_use_selected_text_backend():
    from agentnexus.services.memories import search_memories

    store = MagicMock()
    store.search_text.return_value = [{"id": 7, "content": "known fact"}]
    model = MagicMock()
    model.encode.return_value = [0.25, 0.75]
    with patch("agentnexus.rag.embeddings.get_embedding_model", return_value=model):
        assert search_memories(store, "known", 3) == [{"id": 7, "content": "known fact"}]
    store.search_text.assert_called_once_with("known", limit=3)
    model.encode.assert_not_called()
