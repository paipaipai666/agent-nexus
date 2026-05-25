"""Tests for KnowledgeBaseService."""

from unittest.mock import MagicMock, patch

from agentnexus.services.knowledge import KnowledgeBaseService


class TestKnowledgeBaseService:
    def test_search_calls_kb_search(self):
        mock_kb_search = MagicMock(return_value="result text")
        with patch("agentnexus.tools.kb_search.kb_search", mock_kb_search):
            service = KnowledgeBaseService(MagicMock())
            result = service.search("test query", namespace="test")

        assert result == "result text"
        mock_kb_search.assert_called_once_with(query="test query", namespace="test")

    def test_search_defaults(self):
        mock_kb_search = MagicMock(return_value="result")
        with patch("agentnexus.tools.kb_search.kb_search", mock_kb_search):
            service = KnowledgeBaseService(MagicMock())
            result = service.search("hello")

        assert result == "result"
        mock_kb_search.assert_called_once_with(query="hello")

    def test_import_document_calls_ingest(self):
        mock_ingest = MagicMock(return_value=["chunk1", "chunk2"])
        with patch("agentnexus.rag.ingestion.ingest", mock_ingest):
            service = KnowledgeBaseService(MagicMock())
            result = service.import_document("/path/to/doc.md", strategy="recursive")

        assert result == ["chunk1", "chunk2"]
        mock_ingest.assert_called_once_with("/path/to/doc.md", strategy="recursive")

    def test_import_document_defaults(self):
        mock_ingest = MagicMock(return_value=[])
        with patch("agentnexus.rag.ingestion.ingest", mock_ingest):
            service = KnowledgeBaseService(MagicMock())
            result = service.import_document("/path/to/doc.md")

        assert result == []
        mock_ingest.assert_called_once_with("/path/to/doc.md")
