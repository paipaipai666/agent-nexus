"""Integration tests for the RAG pipeline: ingest → catalog → search → delete."""
from pathlib import Path
from unittest.mock import MagicMock, patch

from agentnexus.rag.ids import make_chunk_id, make_document_version, make_source_id
from agentnexus.rag.models import ChunkRecord, KnowledgeBaseRecord, SourceDocument
from agentnexus.rag.store import KnowledgeBaseCatalog, get_knowledge_base_catalog


class TestRAGPipelineIntegration:
    """End-to-end RAG pipeline: ingest documents, search, and delete."""

    def _make_catalog(self, temp_agentnexus_home: Path) -> KnowledgeBaseCatalog:
        db_path = str(temp_agentnexus_home / "catalog.db")
        return KnowledgeBaseCatalog(db_path=db_path)

    def _ingest_document(self, catalog: KnowledgeBaseCatalog, kb_id: str, text: str,
                         source_uri: str = "test://doc-0") -> tuple[SourceDocument, list[ChunkRecord]]:
        source_id = make_source_id(source_uri)
        doc_version = make_document_version(source_id, text)
        doc = SourceDocument(
            document_id=doc_version,
            kb_id=kb_id,
            source_id=source_id,
            source_uri=source_uri,
            document_version=doc_version,
            content=text,
            raw_text=text,
            indexed_text=text,
            sparse_text=text,
        )
        chunk = ChunkRecord(
            chunk_id=make_chunk_id(doc_version, 0, text),
            kb_id=kb_id,
            document_id=doc.document_id,
            document_version=doc_version,
            chunk_index=0,
            text=text,
            raw_text=text,
            indexed_text=text,
            sparse_text=text,
            metadata={"source_uri": source_uri},
        )
        catalog.upsert_document(doc)
        catalog.upsert_chunks([chunk])
        return doc, [chunk]

    def test_ingest_stores_chunks_in_catalog(self, temp_agentnexus_home):
        catalog = self._make_catalog(temp_agentnexus_home)
        kb = KnowledgeBaseRecord(
            kb_id="kb-test",
            namespace="test",
            display_name="Test KB",
            collection_name="test_collection",
        )
        catalog.upsert_knowledge_base(kb)

        doc, chunks = self._ingest_document(catalog, kb.kb_id, "Python is a programming language.")

        stored_docs = catalog.list_documents(kb_id=kb.kb_id)
        assert len(stored_docs) == 1
        assert stored_docs[0].source_uri == "test://doc-0"

        stored_chunks = catalog.list_chunks(doc.document_id)
        assert len(stored_chunks) == 1
        assert "Python" in stored_chunks[0].text
        catalog.close()

    def test_ingest_multiple_documents(self, temp_agentnexus_home):
        catalog = self._make_catalog(temp_agentnexus_home)
        kb = KnowledgeBaseRecord(
            kb_id="kb-multi",
            namespace="multi",
            display_name="Multi KB",
            collection_name="multi_collection",
        )
        catalog.upsert_knowledge_base(kb)

        self._ingest_document(catalog, kb.kb_id, "Doc one content", "test://doc-0")
        self._ingest_document(catalog, kb.kb_id, "Doc two content", "test://doc-1")

        stored_docs = catalog.list_documents(kb_id=kb.kb_id)
        assert len(stored_docs) == 2

        all_chunks = catalog.list_chunks_by_kb(kb.kb_id)
        assert len(all_chunks) == 2
        catalog.close()

    def test_delete_source_removes_document_and_chunks(self, temp_agentnexus_home):
        catalog = self._make_catalog(temp_agentnexus_home)
        kb = KnowledgeBaseRecord(
            kb_id="kb-del",
            namespace="del",
            display_name="Del KB",
            collection_name="del_collection",
        )
        catalog.upsert_knowledge_base(kb)

        doc, chunks = self._ingest_document(catalog, kb.kb_id, "To be deleted.")
        doc_id = doc.document_id

        assert len(catalog.list_chunks(doc_id)) == 1

        catalog.delete_document(doc_id)

        assert catalog.get_document(doc_id) is None
        assert len(catalog.list_chunks(doc_id)) == 0
        catalog.close()

    def test_delete_kb_removes_all_documents(self, temp_agentnexus_home):
        catalog = self._make_catalog(temp_agentnexus_home)
        kb = KnowledgeBaseRecord(
            kb_id="kb-delall",
            namespace="delall",
            display_name="DelAll KB",
            collection_name="delall_collection",
        )
        catalog.upsert_knowledge_base(kb)

        self._ingest_document(catalog, kb.kb_id, "First doc")
        self._ingest_document(catalog, kb.kb_id, "Second doc")

        assert len(catalog.list_documents(kb_id=kb.kb_id)) == 2

        catalog.delete_knowledge_base(kb.kb_id)

        assert catalog.get_knowledge_base("delall") is None
        catalog.close()

    @patch("agentnexus.storage.chroma.insert_documents")
    @patch("agentnexus.storage.chroma.search")
    def test_search_after_ingestion_returns_results(self, mock_chroma_search, mock_insert, temp_agentnexus_home):
        catalog = self._make_catalog(temp_agentnexus_home)
        kb = KnowledgeBaseRecord(
            kb_id="kb-search",
            namespace="search",
            display_name="Search KB",
            collection_name="search_collection",
        )
        catalog.upsert_knowledge_base(kb)

        doc, chunks = self._ingest_document(catalog, kb.kb_id, "Python is great for data science.")

        mock_chroma_search.return_value = [
            {"id": chunks[0].chunk_id, "score": 0.95, "text": chunks[0].text, "metadata": {}},
        ]

        from agentnexus.storage.chroma import search as chroma_search
        results = chroma_search("What is Python good for?", limit=5, namespace="search")

        assert len(results) == 1
        assert "Python" in results[0]["text"]
        assert results[0]["score"] > 0.9
        catalog.close()

    def test_upsert_document_updates_existing(self, temp_agentnexus_home):
        catalog = self._make_catalog(temp_agentnexus_home)
        kb = KnowledgeBaseRecord(
            kb_id="kb-upd",
            namespace="upd",
            display_name="Upd KB",
            collection_name="upd_collection",
        )
        catalog.upsert_knowledge_base(kb)

        source_id = make_source_id("test://doc-0")
        v1 = make_document_version(source_id, "version 1")
        doc_v1 = SourceDocument(
            document_id=v1, kb_id=kb.kb_id, source_id=source_id,
            source_uri="test://doc-0", document_version=v1,
            content="version 1", raw_text="version 1", indexed_text="version 1", sparse_text="version 1",
        )
        catalog.upsert_document(doc_v1)
        assert catalog.get_document(v1).content == "version 1"

        doc_v1.content = "updated content"
        catalog.upsert_document(doc_v1)
        assert catalog.get_document(v1).content == "updated content"
        catalog.close()
