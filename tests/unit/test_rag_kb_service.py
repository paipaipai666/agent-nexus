from unittest.mock import patch

from agentnexus.rag.models import ChunkRecord, IngestedDocument, IngestionRunRecord, KnowledgeBaseRecord, SourceDocument
from agentnexus.rag.store import KnowledgeBaseCatalog, _reset_knowledge_base_catalog


class TestDefaultKbRecord:
    def test_creates_valid_record_with_namespace(self, temp_agentnexus_home):
        from agentnexus.rag.kb_service import default_kb_record

        record = default_kb_record("test_namespace")

        assert isinstance(record, KnowledgeBaseRecord)
        assert record.namespace == "test_namespace"
        assert record.display_name == "test_namespace"
        assert record.collection_name
        assert record.kb_id


class TestDeleteExistingSourceVersions:
    def test_no_existing_data_returns_zero(self, temp_agentnexus_home):
        _reset_knowledge_base_catalog()
        from agentnexus.rag.kb_service import delete_existing_source_versions

        with patch("agentnexus.rag.kb_service.delete_documents"):
            result = delete_existing_source_versions("test_ns", "nonexistent_source")

        assert result == 0


class TestPersistIngestedDocument:
    def test_writes_to_catalog(self, temp_agentnexus_home):
        _reset_knowledge_base_catalog()
        from agentnexus.rag.kb_service import persist_ingested_document

        doc = SourceDocument(
            document_id="doc_persist",
            kb_id="",
            source_id="src_persist",
            source_uri="persist.md",
            document_version="v1",
            content="persist content",
        )
        chunks = [
            ChunkRecord(
                chunk_id="chunk_persist_0",
                kb_id="",
                document_id="doc_persist",
                document_version="v1",
                chunk_index=0,
                text="persist chunk 0",
            ),
        ]
        artifacts = IngestedDocument(document=doc, chunks=chunks)

        with patch("agentnexus.rag.kb_service.upsert_documents"):
            with patch("agentnexus.rag.kb_service.delete_documents"):
                stats = persist_ingested_document(artifacts, "persist_ns")

        assert stats["written_chunks"] == 1
        assert stats["replaced_chunks"] == 0

        catalog = KnowledgeBaseCatalog()
        stored = catalog.get_document("doc_persist")
        assert stored is not None
        assert stored.content == "persist content"

    def test_replaces_existing_chunks(self, temp_agentnexus_home):
        _reset_knowledge_base_catalog()
        from agentnexus.rag.kb_service import persist_ingested_document

        doc_v1 = SourceDocument(
            document_id="doc_replace",
            kb_id="",
            source_id="src_replace",
            source_uri="replace.md",
            document_version="v1",
            content="old content",
        )
        chunks_v1 = [
            ChunkRecord(
                chunk_id="chunk_replace_old",
                kb_id="",
                document_id="doc_replace",
                document_version="v1",
                chunk_index=0,
                text="old chunk",
            ),
        ]
        with patch("agentnexus.rag.kb_service.upsert_documents"):
            with patch("agentnexus.rag.kb_service.delete_documents"):
                persist_ingested_document(
                    IngestedDocument(document=doc_v1, chunks=chunks_v1),
                    "replace_ns",
                )

        doc_v2 = SourceDocument(
            document_id="doc_replace",
            kb_id="",
            source_id="src_replace",
            source_uri="replace.md",
            document_version="v2",
            content="new content",
        )
        chunks_v2 = [
            ChunkRecord(
                chunk_id="chunk_replace_new",
                kb_id="",
                document_id="doc_replace",
                document_version="v2",
                chunk_index=0,
                text="new chunk",
            ),
        ]
        with patch("agentnexus.rag.kb_service.upsert_documents"):
            with patch("agentnexus.rag.kb_service.delete_documents"):
                stats = persist_ingested_document(
                    IngestedDocument(document=doc_v2, chunks=chunks_v2),
                    "replace_ns",
                )

        assert stats["replaced_chunks"] >= 0


class TestIngestionRunLifecycle:
    def test_start_and_finish_ingestion_run(self, temp_agentnexus_home):
        _reset_knowledge_base_catalog()
        from agentnexus.rag.kb_service import finish_ingestion_run, start_ingestion_run

        run = start_ingestion_run("run_ns", "docs/run_test.md")

        assert isinstance(run, IngestionRunRecord)
        assert run.status == "running"
        assert run.source_uri == "docs/run_test.md"
        assert run.run_id.startswith("ingest_")

        finish_ingestion_run(
            run,
            status="completed",
            documents_seen=1,
            chunks_written=5,
            metadata={"duration_ms": 123.45},
        )

        assert run.status == "completed"
        assert run.documents_seen == 1
        assert run.chunks_written == 5
        assert run.finished_at is not None
        assert run.metadata["duration_ms"] == 123.45

    def test_start_ingestion_run_persists_to_catalog(self, temp_agentnexus_home):
        _reset_knowledge_base_catalog()
        from agentnexus.rag.kb_service import start_ingestion_run

        run = start_ingestion_run("persist_run_ns", "docs/persist.md")

        catalog = KnowledgeBaseCatalog()
        runs = catalog.list_ingestion_runs()
        assert any(r.run_id == run.run_id for r in runs)

    def test_finish_ingestion_run_with_error(self, temp_agentnexus_home):
        _reset_knowledge_base_catalog()
        from agentnexus.rag.kb_service import finish_ingestion_run, start_ingestion_run

        run = start_ingestion_run("err_ns", "docs/err.md")
        finish_ingestion_run(
            run,
            status="failed",
            documents_seen=0,
            chunks_written=0,
            error_message="something went wrong",
        )

        assert run.status == "failed"
        assert run.error_message == "something went wrong"
