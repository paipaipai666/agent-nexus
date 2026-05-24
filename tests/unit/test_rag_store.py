from agentnexus.rag.ids import make_chunk_id, make_document_version, make_source_id
from agentnexus.rag.models import (
    ChunkRecord,
    DocumentSection,
    IngestionRunRecord,
    KnowledgeBaseRecord,
    SourceDocument,
)
from agentnexus.rag.store import KnowledgeBaseCatalog


class TestKnowledgeBaseCatalog:
    def test_catalog_initializes_core_tables(self, temp_agentnexus_home):
        catalog = KnowledgeBaseCatalog()

        rows = catalog._conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
        table_names = {row["name"] for row in rows}

        assert {"knowledge_bases", "source_documents", "document_chunks", "ingestion_runs"} <= table_names

    def test_upsert_and_list_records(self, temp_agentnexus_home):
        catalog = KnowledgeBaseCatalog()
        kb = KnowledgeBaseRecord(
            kb_id="kb_support",
            namespace="support",
            display_name="Support KB",
            collection_name="kb_support",
            metadata={"locale": "zh-CN"},
        )
        catalog.upsert_knowledge_base(kb)

        source_id = make_source_id("docs/support.md")
        document_version = make_document_version(source_id, "support content")
        document = SourceDocument(
            document_id=document_version,
            kb_id="kb_support",
            source_id=source_id,
            source_uri="docs/support.md",
            document_version=document_version,
            content="support content",
            metadata={"kind": "markdown"},
        )
        catalog.upsert_document(document)

        chunk = ChunkRecord(
            chunk_id=make_chunk_id(document_version, 0, "support chunk"),
            kb_id="kb_support",
            document_id=document.document_id,
            document_version=document_version,
            chunk_index=0,
            text="support chunk",
            metadata={"token_count": 2},
        )
        catalog.upsert_chunks([chunk])

        run = IngestionRunRecord(
            run_id="run-1",
            kb_id="kb_support",
            status="completed",
            source_uri="docs/support.md",
            documents_seen=1,
            chunks_written=1,
        )
        catalog.upsert_ingestion_run(run)

        listed_kbs = catalog.list_knowledge_bases()
        listed_documents = catalog.list_documents("kb_support")
        listed_chunks = catalog.list_chunks(document.document_id)
        listed_runs = catalog.list_ingestion_runs("kb_support")

        assert len(listed_kbs) == 1
        assert listed_kbs[0].metadata["locale"] == "zh-CN"
        assert listed_documents[0].document_version == document_version
        assert listed_documents[0].metadata["kind"] == "markdown"
        assert listed_chunks[0].chunk_id == chunk.chunk_id
        assert listed_runs[0].status == "completed"

    def test_structured_fields_round_trip_through_catalog(self, temp_agentnexus_home):
        catalog = KnowledgeBaseCatalog()
        catalog.upsert_knowledge_base(
            KnowledgeBaseRecord(
                kb_id="kb_support",
                namespace="support",
                display_name="Support KB",
                collection_name="kb_support",
            )
        )
        source_id = make_source_id("docs/guide.md")
        raw_text = "# Guide\n\nBody text"
        document_version = make_document_version(source_id, raw_text, {"format": "markdown"})
        sections = [
            DocumentSection.create(
                document_version,
                section_index=0,
                raw_text="Body text",
                indexed_text="Guide\n\nBody text",
                sparse_text="Guide\n\nBody text",
                metadata={
                    "format": "markdown",
                    "heading_path": ["Guide"],
                    "section_title": "Guide",
                },
            )
        ]
        document = SourceDocument(
            document_id=document_version,
            kb_id="kb_support",
            source_id=source_id,
            source_uri="docs/guide.md",
            document_version=document_version,
            content=raw_text,
            metadata={"format": "markdown", "kind": "guide"},
            raw_text=raw_text,
            indexed_text="Guide\n\nBody text",
            sparse_text="guide body text",
            sections=sections,
        )
        chunk = ChunkRecord(
            chunk_id=make_chunk_id(document_version, 0, "Guide\n\nBody text", {"section_index": 0}),
            kb_id="kb_support",
            document_id=document.document_id,
            document_version=document_version,
            chunk_index=0,
            text="Guide\n\nBody text",
            metadata={
                "format": "markdown",
                "heading_path": ["Guide"],
                "section_title": "Guide",
                "section_index": 0,
                "page_number": 3,
                "block_type": "paragraph",
                "has_code": False,
                "has_list": False,
                "heading_depth": 1,
            },
            raw_text="Body text",
            indexed_text="Guide\n\nBody text",
            sparse_text="guide body text",
            section_index=0,
            page_number=3,
        )

        catalog.upsert_document(document)
        catalog.upsert_chunks([chunk])

        listed_document = catalog.list_documents("kb_support")[0]
        listed_chunk = catalog.list_chunks(document.document_id)[0]

        assert listed_document.raw_text == raw_text
        assert listed_document.indexed_text == "Guide\n\nBody text"
        assert listed_document.sparse_text == "guide body text"
        assert len(listed_document.sections) == 1
        assert listed_document.sections[0].metadata["heading_path"] == ["Guide"]
        assert listed_document.sections[0].section_index == 0
        assert listed_chunk.raw_text == "Body text"
        assert listed_chunk.indexed_text == "Guide\n\nBody text"
        assert listed_chunk.sparse_text == "guide body text"
        assert listed_chunk.section_index == 0
        assert listed_chunk.page_number == 3
        assert listed_chunk.metadata["heading_path"] == ["Guide"]
        assert listed_chunk.metadata["block_type"] == "paragraph"
        assert listed_chunk.metadata["heading_depth"] == 1

    def test_get_and_delete_knowledge_base(self, temp_agentnexus_home):
        catalog = KnowledgeBaseCatalog()
        record = KnowledgeBaseRecord(
            kb_id="kb_support",
            namespace="support",
            display_name="Support KB",
            collection_name="kb_support",
        )
        catalog.upsert_knowledge_base(record)

        fetched = catalog.get_knowledge_base("support")
        assert fetched is not None
        assert fetched.kb_id == "kb_support"

        catalog.delete_knowledge_base("kb_support")
        assert catalog.get_knowledge_base("support") is None

    def test_upsert_documents_supports_batch_write(self, temp_agentnexus_home):
        catalog = KnowledgeBaseCatalog()
        catalog.upsert_knowledge_base(
            KnowledgeBaseRecord(
                kb_id="kb_support",
                namespace="support",
                display_name="Support KB",
                collection_name="kb_support",
            )
        )
        doc_a = SourceDocument(
            document_id="doc_a",
            kb_id="kb_support",
            source_id="src_a",
            source_uri="docs/a.md",
            document_version="v1",
            content="alpha",
        )
        doc_b = SourceDocument(
            document_id="doc_b",
            kb_id="kb_support",
            source_id="src_b",
            source_uri="docs/b.md",
            document_version="v1",
            content="beta",
        )

        catalog.upsert_documents([doc_a, doc_b])

        assert [doc.document_id for doc in catalog.list_documents("kb_support")] == ["doc_a", "doc_b"]

    def test_list_chunks_by_kb_returns_all_chunks(self, temp_agentnexus_home):
        catalog = KnowledgeBaseCatalog()
        catalog.upsert_knowledge_base(
            KnowledgeBaseRecord(
                kb_id="kb_support",
                namespace="support",
                display_name="Support KB",
                collection_name="kb_support",
            )
        )
        source_id = make_source_id("docs/a.md")
        document_version = make_document_version(source_id, "body")
        document = SourceDocument(
            document_id=document_version,
            kb_id="kb_support",
            source_id=source_id,
            source_uri="docs/a.md",
            document_version=document_version,
            content="body",
        )
        catalog.upsert_document(document)
        chunk_a = ChunkRecord(
            chunk_id=make_chunk_id(document_version, 0, "alpha"),
            kb_id="kb_support",
            document_id=document.document_id,
            document_version=document_version,
            chunk_index=0,
            text="alpha",
        )
        chunk_b = ChunkRecord(
            chunk_id=make_chunk_id(document_version, 1, "beta"),
            kb_id="kb_support",
            document_id=document.document_id,
            document_version=document_version,
            chunk_index=1,
            text="beta",
        )
        catalog.upsert_chunks([chunk_a, chunk_b])

        chunks = catalog.list_chunks_by_kb("kb_support")
        assert [chunk.text for chunk in chunks] == ["alpha", "beta"]

    def test_list_section_chunks_returns_same_section_only(self, temp_agentnexus_home):
        catalog = KnowledgeBaseCatalog()
        catalog.upsert_knowledge_base(
            KnowledgeBaseRecord(
                kb_id="kb_support",
                namespace="support",
                display_name="Support KB",
                collection_name="kb_support",
            )
        )
        source_id = make_source_id("docs/a.md")
        document_version = make_document_version(source_id, "body")
        document = SourceDocument(
            document_id=document_version,
            kb_id="kb_support",
            source_id=source_id,
            source_uri="docs/a.md",
            document_version=document_version,
            content="body",
        )
        catalog.upsert_document(document)
        chunk_a = ChunkRecord(
            chunk_id=make_chunk_id(document_version, 0, "alpha", {"section_index": 0}),
            kb_id="kb_support",
            document_id=document.document_id,
            document_version=document_version,
            chunk_index=0,
            text="alpha",
            metadata={"section_index": 0},
        )
        chunk_b = ChunkRecord(
            chunk_id=make_chunk_id(document_version, 1, "beta", {"section_index": 0}),
            kb_id="kb_support",
            document_id=document.document_id,
            document_version=document_version,
            chunk_index=1,
            text="beta",
            metadata={"section_index": 0},
        )
        chunk_c = ChunkRecord(
            chunk_id=make_chunk_id(document_version, 2, "gamma", {"section_index": 1}),
            kb_id="kb_support",
            document_id=document.document_id,
            document_version=document_version,
            chunk_index=2,
            text="gamma",
            metadata={"section_index": 1},
        )
        catalog.upsert_chunks([chunk_a, chunk_b, chunk_c])

        section_chunks = catalog.list_section_chunks(document.document_id, 0)

        assert [chunk.text for chunk in section_chunks] == ["alpha", "beta"]

    def test_list_documents_by_source_and_delete_document(self, temp_agentnexus_home):
        catalog = KnowledgeBaseCatalog()
        catalog.upsert_knowledge_base(
            KnowledgeBaseRecord(
                kb_id="kb_support",
                namespace="support",
                display_name="Support KB",
                collection_name="kb_support",
            )
        )
        source_id = make_source_id("docs/a.md")
        document_a = SourceDocument(
            document_id="doc_a",
            kb_id="kb_support",
            source_id=source_id,
            source_uri="docs/a.md",
            document_version="v1",
            content="alpha",
        )
        document_b = SourceDocument(
            document_id="doc_b",
            kb_id="kb_support",
            source_id=source_id,
            source_uri="docs/a.md",
            document_version="v2",
            content="beta",
        )
        catalog.upsert_documents([document_a, document_b])

        matched = catalog.list_documents_by_source("kb_support", source_id)
        assert [doc.document_id for doc in matched] == ["doc_a", "doc_b"]

        catalog.delete_document("doc_a")
        assert catalog.get_document("doc_a") is None
