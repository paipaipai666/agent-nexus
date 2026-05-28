from agentnexus.rag.ids import make_chunk_id, make_document_version, make_source_id
from agentnexus.rag.models import (
    ChunkRecord,
    DocumentSection,
    IngestionRunRecord,
    KnowledgeBaseRecord,
    SourceDocument,
)
from agentnexus.rag.store import KnowledgeBaseCatalog, _decode_sections, _encode_sections, _reset_knowledge_base_catalog


class TestKnowledgeBaseCatalogRoundTrips:
    def test_upsert_and_get_knowledge_base(self, temp_agentnexus_home):
        _reset_knowledge_base_catalog()
        catalog = KnowledgeBaseCatalog()
        record = KnowledgeBaseRecord(
            kb_id="kb_alpha",
            namespace="alpha",
            display_name="Alpha KB",
            collection_name="kb_alpha",
            description="Test knowledge base",
            metadata={"lang": "en"},
        )
        catalog.upsert_knowledge_base(record)

        fetched = catalog.get_knowledge_base("alpha")
        assert fetched is not None
        assert fetched.kb_id == "kb_alpha"
        assert fetched.namespace == "alpha"
        assert fetched.display_name == "Alpha KB"
        assert fetched.description == "Test knowledge base"
        assert fetched.metadata == {"lang": "en"}

    def test_list_knowledge_bases_sorted_by_namespace(self, temp_agentnexus_home):
        _reset_knowledge_base_catalog()
        catalog = KnowledgeBaseCatalog()
        for ns in ["zebra", "alpha", "middle"]:
            catalog.upsert_knowledge_base(
                KnowledgeBaseRecord(
                    kb_id=f"kb_{ns}",
                    namespace=ns,
                    display_name=f"{ns} KB",
                    collection_name=f"kb_{ns}",
                )
            )

        result = catalog.list_knowledge_bases()
        assert [r.namespace for r in result] == ["alpha", "middle", "zebra"]

    def test_delete_knowledge_base(self, temp_agentnexus_home):
        _reset_knowledge_base_catalog()
        catalog = KnowledgeBaseCatalog()
        catalog.upsert_knowledge_base(
            KnowledgeBaseRecord(
                kb_id="kb_del",
                namespace="del",
                display_name="Del KB",
                collection_name="kb_del",
            )
        )
        assert catalog.get_knowledge_base("del") is not None

        catalog.delete_knowledge_base("kb_del")
        assert catalog.get_knowledge_base("del") is None

    def test_upsert_and_get_document(self, temp_agentnexus_home):
        _reset_knowledge_base_catalog()
        catalog = KnowledgeBaseCatalog()
        catalog.upsert_knowledge_base(
            KnowledgeBaseRecord(
                kb_id="kb_doc",
                namespace="doc",
                display_name="Doc KB",
                collection_name="kb_doc",
            )
        )
        source_id = make_source_id("docs/test.md")
        document_version = make_document_version(source_id, "hello world")
        doc = SourceDocument(
            document_id=document_version,
            kb_id="kb_doc",
            source_id=source_id,
            source_uri="docs/test.md",
            document_version=document_version,
            content="hello world",
            metadata={"type": "markdown"},
        )
        catalog.upsert_document(doc)

        fetched = catalog.get_document(document_version)
        assert fetched is not None
        assert fetched.content == "hello world"
        assert fetched.metadata == {"type": "markdown"}

    def test_list_documents_with_kb_id_filter(self, temp_agentnexus_home):
        _reset_knowledge_base_catalog()
        catalog = KnowledgeBaseCatalog()
        for ns in ["kb_a", "kb_b"]:
            catalog.upsert_knowledge_base(
                KnowledgeBaseRecord(
                    kb_id=ns,
                    namespace=ns,
                    display_name=ns,
                    collection_name=ns,
                )
            )
        doc_a = SourceDocument(
            document_id="doc_a",
            kb_id="kb_a",
            source_id="src_a",
            source_uri="a.md",
            document_version="v1",
            content="alpha",
        )
        doc_b = SourceDocument(
            document_id="doc_b",
            kb_id="kb_b",
            source_id="src_b",
            source_uri="b.md",
            document_version="v1",
            content="beta",
        )
        catalog.upsert_documents([doc_a, doc_b])

        assert len(catalog.list_documents("kb_a")) == 1
        assert catalog.list_documents("kb_a")[0].document_id == "doc_a"
        assert len(catalog.list_documents()) == 2

    def test_list_documents_by_source(self, temp_agentnexus_home):
        _reset_knowledge_base_catalog()
        catalog = KnowledgeBaseCatalog()
        catalog.upsert_knowledge_base(
            KnowledgeBaseRecord(
                kb_id="kb_src",
                namespace="src",
                display_name="Src KB",
                collection_name="kb_src",
            )
        )
        source_id = make_source_id("docs/multi.md")
        for i in range(3):
            doc = SourceDocument(
                document_id=f"doc_{i}",
                kb_id="kb_src",
                source_id=source_id,
                source_uri="docs/multi.md",
                document_version=f"v{i}",
                content=f"version {i}",
            )
            catalog.upsert_document(doc)

        result = catalog.list_documents_by_source("kb_src", source_id)
        assert len(result) == 3
        assert all(d.source_id == source_id for d in result)

    def test_upsert_and_list_chunks(self, temp_agentnexus_home):
        _reset_knowledge_base_catalog()
        catalog = KnowledgeBaseCatalog()
        catalog.upsert_knowledge_base(
            KnowledgeBaseRecord(
                kb_id="kb_chunk",
                namespace="chunk",
                display_name="Chunk KB",
                collection_name="kb_chunk",
            )
        )
        source_id = make_source_id("docs/chunks.md")
        document_version = make_document_version(source_id, "chunk content")
        doc = SourceDocument(
            document_id=document_version,
            kb_id="kb_chunk",
            source_id=source_id,
            source_uri="docs/chunks.md",
            document_version=document_version,
            content="chunk content",
        )
        catalog.upsert_document(doc)

        chunks = [
            ChunkRecord(
                chunk_id=make_chunk_id(document_version, i, f"text_{i}"),
                kb_id="kb_chunk",
                document_id=document_version,
                document_version=document_version,
                chunk_index=i,
                text=f"text_{i}",
            )
            for i in range(3)
        ]
        catalog.upsert_chunks(chunks)

        result = catalog.list_chunks(document_version)
        assert len(result) == 3
        assert [c.text for c in result] == ["text_0", "text_1", "text_2"]

    def test_list_chunks_by_kb(self, temp_agentnexus_home):
        _reset_knowledge_base_catalog()
        catalog = KnowledgeBaseCatalog()
        catalog.upsert_knowledge_base(
            KnowledgeBaseRecord(
                kb_id="kb_ckb",
                namespace="ckb",
                display_name="CKB",
                collection_name="kb_ckb",
            )
        )
        doc_ids = ["doc_1", "doc_2"]
        for doc_id in doc_ids:
            doc = SourceDocument(
                document_id=doc_id,
                kb_id="kb_ckb",
                source_id="src",
                source_uri="x.md",
                document_version="v1",
                content="c",
            )
            catalog.upsert_document(doc)
            catalog.upsert_chunks([
                ChunkRecord(
                    chunk_id=f"chunk_{doc_id}_{i}",
                    kb_id="kb_ckb",
                    document_id=doc_id,
                    document_version="v1",
                    chunk_index=i,
                    text=f"{doc_id}_chunk_{i}",
                )
                for i in range(2)
            ])

        result = catalog.list_chunks_by_kb("kb_ckb")
        assert len(result) == 4

    def test_list_neighbor_chunks_with_window(self, temp_agentnexus_home):
        _reset_knowledge_base_catalog()
        catalog = KnowledgeBaseCatalog()
        catalog.upsert_knowledge_base(
            KnowledgeBaseRecord(
                kb_id="kb_nbr",
                namespace="nbr",
                display_name="Nbr KB",
                collection_name="kb_nbr",
            )
        )
        doc_id = "doc_nbr"
        doc = SourceDocument(
            document_id=doc_id,
            kb_id="kb_nbr",
            source_id="src",
            source_uri="nbr.md",
            document_version="v1",
            content="neighbor test",
        )
        catalog.upsert_document(doc)
        catalog.upsert_chunks([
            ChunkRecord(
                chunk_id=f"chunk_nbr_{i}",
                kb_id="kb_nbr",
                document_id=doc_id,
                document_version="v1",
                chunk_index=i,
                text=f"chunk_{i}",
            )
            for i in range(5)
        ])

        neighbors = catalog.list_neighbor_chunks(doc_id, chunk_index=2, window=1)
        assert [c.chunk_index for c in neighbors] == [1, 2, 3]

    def test_list_neighbor_chunks_window_zero_returns_all(self, temp_agentnexus_home):
        _reset_knowledge_base_catalog()
        catalog = KnowledgeBaseCatalog()
        catalog.upsert_knowledge_base(
            KnowledgeBaseRecord(
                kb_id="kb_nbr0",
                namespace="nbr0",
                display_name="Nbr0",
                collection_name="kb_nbr0",
            )
        )
        doc_id = "doc_nbr0"
        catalog.upsert_document(SourceDocument(
            document_id=doc_id,
            kb_id="kb_nbr0",
            source_id="src",
            source_uri="nbr0.md",
            document_version="v1",
            content="c",
        ))
        catalog.upsert_chunks([
            ChunkRecord(
                chunk_id=f"chunk_nbr0_{i}",
                kb_id="kb_nbr0",
                document_id=doc_id,
                document_version="v1",
                chunk_index=i,
                text=f"c{i}",
            )
            for i in range(4)
        ])

        result = catalog.list_neighbor_chunks(doc_id, chunk_index=2, window=0)
        assert len(result) == 4

    def test_list_section_chunks_with_section_index(self, temp_agentnexus_home):
        _reset_knowledge_base_catalog()
        catalog = KnowledgeBaseCatalog()
        catalog.upsert_knowledge_base(
            KnowledgeBaseRecord(
                kb_id="kb_sec",
                namespace="sec",
                display_name="Sec KB",
                collection_name="kb_sec",
            )
        )
        doc_id = "doc_sec"
        catalog.upsert_document(SourceDocument(
            document_id=doc_id,
            kb_id="kb_sec",
            source_id="src",
            source_uri="sec.md",
            document_version="v1",
            content="c",
        ))
        catalog.upsert_chunks([
            ChunkRecord(
                chunk_id=f"chunk_sec_{i}",
                kb_id="kb_sec",
                document_id=doc_id,
                document_version="v1",
                chunk_index=i,
                text=f"c{i}",
                section_index=i % 2,
            )
            for i in range(4)
        ])

        sec0 = catalog.list_section_chunks(doc_id, section_index=0)
        sec1 = catalog.list_section_chunks(doc_id, section_index=1)
        assert len(sec0) == 2
        assert len(sec1) == 2
        assert all(c.section_index == 0 for c in sec0)
        assert all(c.section_index == 1 for c in sec1)

    def test_delete_document_cascades_to_chunks(self, temp_agentnexus_home):
        _reset_knowledge_base_catalog()
        catalog = KnowledgeBaseCatalog()
        catalog.upsert_knowledge_base(
            KnowledgeBaseRecord(
                kb_id="kb_cascade",
                namespace="cascade",
                display_name="Cascade",
                collection_name="kb_cascade",
            )
        )
        doc_id = "doc_cascade"
        catalog.upsert_document(SourceDocument(
            document_id=doc_id,
            kb_id="kb_cascade",
            source_id="src",
            source_uri="cascade.md",
            document_version="v1",
            content="c",
        ))
        catalog.upsert_chunks([
            ChunkRecord(
                chunk_id=f"chunk_cascade_{i}",
                kb_id="kb_cascade",
                document_id=doc_id,
                document_version="v1",
                chunk_index=i,
                text=f"c{i}",
            )
            for i in range(3)
        ])
        assert len(catalog.list_chunks(doc_id)) == 3

        catalog.delete_document(doc_id)
        assert catalog.get_document(doc_id) is None
        assert len(catalog.list_chunks(doc_id)) == 0

    def test_upsert_and_list_ingestion_runs(self, temp_agentnexus_home):
        _reset_knowledge_base_catalog()
        catalog = KnowledgeBaseCatalog()
        catalog.upsert_knowledge_base(
            KnowledgeBaseRecord(
                kb_id="kb_run",
                namespace="run",
                display_name="Run KB",
                collection_name="kb_run",
            )
        )
        run = IngestionRunRecord(
            run_id="run_001",
            kb_id="kb_run",
            status="completed",
            source_uri="docs/run.md",
            documents_seen=1,
            chunks_written=5,
        )
        catalog.upsert_ingestion_run(run)

        result = catalog.list_ingestion_runs("kb_run")
        assert len(result) == 1
        assert result[0].run_id == "run_001"
        assert result[0].status == "completed"
        assert result[0].documents_seen == 1

    def test_migrate_schema_idempotent(self, temp_agentnexus_home):
        _reset_knowledge_base_catalog()
        catalog = KnowledgeBaseCatalog()
        catalog._migrate_schema()
        catalog._migrate_schema()

        rows = catalog._conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
        table_names = {row["name"] for row in rows}
        assert {"knowledge_bases", "source_documents", "document_chunks", "ingestion_runs"} <= table_names

    def test_encode_decode_sections_round_trip(self):
        sections = [
            DocumentSection(
                section_id="sec_0",
                section_index=0,
                raw_text="raw",
                indexed_text="indexed",
                sparse_text="sparse",
                metadata={"heading": "Intro"},
                page_number=1,
            ),
            DocumentSection(
                section_id="sec_1",
                section_index=1,
                raw_text="body",
                indexed_text="body idx",
                sparse_text="body sparse",
                metadata={},
                page_number=None,
            ),
        ]

        encoded = _encode_sections(sections)
        decoded = _decode_sections(encoded)

        assert len(decoded) == 2
        assert decoded[0].section_id == "sec_0"
        assert decoded[0].raw_text == "raw"
        assert decoded[0].metadata == {"heading": "Intro"}
        assert decoded[0].page_number == 1
        assert decoded[1].section_id == "sec_1"
        assert decoded[1].page_number is None

    def test_decode_sections_empty_returns_empty_list(self):
        assert _decode_sections(None) == []
        assert _decode_sections("") == []
        assert _decode_sections("[]") == []
