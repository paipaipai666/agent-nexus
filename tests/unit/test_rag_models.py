from agentnexus.rag.models import (
    ChunkRecord,
    DocumentSection,
    IngestedDocument,
    IngestionRunRecord,
    KnowledgeBaseRecord,
    SourceDocument,
)


class TestDocumentSection:
    def test_direct_init(self):
        section = DocumentSection(
            section_id="sec_001",
            section_index=0,
            raw_text="raw text",
            indexed_text="indexed text",
            sparse_text="sparse text",
            metadata={"key": "value"},
            page_number=42,
        )
        assert section.section_id == "sec_001"
        assert section.section_index == 0
        assert section.raw_text == "raw text"
        assert section.indexed_text == "indexed text"
        assert section.sparse_text == "sparse text"
        assert section.metadata == {"key": "value"}
        assert section.page_number == 42

    def test_page_from_metadata(self):
        section = DocumentSection(
            section_id="sec_002",
            section_index=1,
            raw_text="text",
            indexed_text="text",
            sparse_text="text",
            metadata={"page_number": 7},
        )
        assert section.page_number == 7

    def test_create_classmethod(self):
        section = DocumentSection.create(
            document_version="doc_v1",
            section_index=0,
            raw_text="hello world",
        )
        assert section.section_index == 0
        assert section.raw_text == "hello world"
        assert section.indexed_text == "hello world"
        assert section.sparse_text == "hello world"
        assert section.metadata == {}
        assert section.page_number is None
        assert section.section_id.startswith("chunk_")


class TestKnowledgeBaseRecord:
    def test_init(self):
        kb = KnowledgeBaseRecord(
            kb_id="kb_test",
            namespace="test",
            display_name="Test KB",
            collection_name="kb_test",
        )
        assert kb.kb_id == "kb_test"
        assert kb.namespace == "test"
        assert kb.display_name == "Test KB"
        assert kb.collection_name == "kb_test"
        assert kb.description == ""
        assert kb.metadata == {}
        assert kb.created_at is None
        assert kb.updated_at is None

    def test_with_all_fields(self):
        kb = KnowledgeBaseRecord(
            kb_id="kb_full",
            namespace="full",
            display_name="Full KB",
            collection_name="kb_full",
            description="A full KB",
            metadata={"locale": "en"},
            created_at="2024-01-01T00:00:00",
            updated_at="2024-01-02T00:00:00",
        )
        assert kb.description == "A full KB"
        assert kb.metadata == {"locale": "en"}
        assert kb.created_at == "2024-01-01T00:00:00"
        assert kb.updated_at == "2024-01-02T00:00:00"


class TestSourceDocument:
    def test_direct_init(self):
        doc = SourceDocument(
            document_id="doc_v1",
            kb_id="kb_test",
            source_id="src_test",
            source_uri="/path/to/doc.md",
            document_version="v1",
            content="test content",
        )
        assert doc.document_id == "doc_v1"
        assert doc.kb_id == "kb_test"
        assert doc.source_id == "src_test"
        assert doc.source_uri == "/path/to/doc.md"
        assert doc.document_version == "v1"
        assert doc.content == "test content"
        assert doc.raw_text == "test content"
        assert doc.indexed_text == "test content"
        assert doc.sparse_text == "test content"
        assert doc.metadata == {}
        assert doc.sections == []

    def test_create_classmethod(self):
        doc = SourceDocument.create(
            source_uri="/path/to/doc.md",
            raw_text="hello world",
            kb_id="kb_test",
        )
        assert doc.source_uri == "/path/to/doc.md"
        assert doc.raw_text == "hello world"
        assert doc.content == "hello world"
        assert doc.indexed_text == "hello world"
        assert doc.sparse_text == "hello world"
        assert doc.kb_id == "kb_test"
        assert doc.source_id.startswith("src_")
        assert doc.document_version.startswith("doc_")
        assert doc.document_id == doc.document_version

    def test_create_with_sections(self):
        sections = [
            DocumentSection.create(
                document_version="doc_v1",
                section_index=0,
                raw_text="section 1",
            ),
            DocumentSection.create(
                document_version="doc_v1",
                section_index=1,
                raw_text="section 2",
            ),
        ]
        doc = SourceDocument.create(
            source_uri="/path/to/doc.md",
            raw_text="hello world",
            sections=sections,
        )
        assert len(doc.sections) == 2
        assert doc.sections[0].section_index == 0
        assert doc.sections[1].raw_text == "section 2"

    def test_create_with_custom_texts(self):
        doc = SourceDocument.create(
            source_uri="/path/to/doc.md",
            raw_text="hello world",
            indexed_text="indexed hello",
            sparse_text="sparse hello",
            metadata={"format": "markdown"},
        )
        assert doc.raw_text == "hello world"
        assert doc.indexed_text == "indexed hello"
        assert doc.sparse_text == "sparse hello"
        assert doc.metadata == {"format": "markdown"}


class TestChunkRecord:
    def test_direct_init(self):
        chunk = ChunkRecord(
            chunk_id="chunk_001",
            kb_id="kb_test",
            document_id="doc_v1",
            document_version="v1",
            chunk_index=0,
            text="chunk text",
        )
        assert chunk.chunk_id == "chunk_001"
        assert chunk.kb_id == "kb_test"
        assert chunk.document_id == "doc_v1"
        assert chunk.document_version == "v1"
        assert chunk.chunk_index == 0
        assert chunk.text == "chunk text"
        assert chunk.raw_text == "chunk text"
        assert chunk.indexed_text == "chunk text"
        assert chunk.sparse_text == "chunk text"
        assert chunk.section_index is None
        assert chunk.page_number is None

    def test_section_page_from_metadata(self):
        chunk = ChunkRecord(
            chunk_id="chunk_002",
            kb_id="kb_test",
            document_id="doc_v1",
            document_version="v1",
            chunk_index=1,
            text="text",
            metadata={"section_index": 3, "page_number": 42},
        )
        assert chunk.section_index == 3
        assert chunk.page_number == 42

    def test_create_classmethod(self):
        doc = SourceDocument(
            document_id="doc_v1",
            kb_id="kb_test",
            source_id="src_test",
            source_uri="/path/to/doc.md",
            document_version="v1",
            content="test content",
        )
        chunk = ChunkRecord.create(
            document=doc,
            chunk_index=0,
            raw_text="chunk content",
        )
        assert chunk.chunk_index == 0
        assert chunk.raw_text == "chunk content"
        assert chunk.text == "chunk content"
        assert chunk.indexed_text == "chunk content"
        assert chunk.sparse_text == "chunk content"
        assert chunk.kb_id == "kb_test"
        assert chunk.document_id == "doc_v1"
        assert chunk.document_version == "v1"
        assert chunk.metadata == {}
        assert chunk.chunk_id.startswith("chunk_")


class TestIngestedDocument:
    def test_init(self):
        doc = SourceDocument(
            document_id="doc_v1",
            kb_id="kb_test",
            source_id="src_test",
            source_uri="/path/to/doc.md",
            document_version="v1",
            content="test content",
        )
        chunks = [
            ChunkRecord(
                chunk_id="chunk_1",
                kb_id="kb_test",
                document_id="doc_v1",
                document_version="v1",
                chunk_index=0,
                text="chunk 1",
            ),
            ChunkRecord(
                chunk_id="chunk_2",
                kb_id="kb_test",
                document_id="doc_v1",
                document_version="v1",
                chunk_index=1,
                text="chunk 2",
            ),
        ]
        ingested = IngestedDocument(document=doc, chunks=chunks)
        assert ingested.document.document_id == "doc_v1"
        assert len(ingested.chunks) == 2

    def test_legacy_chunks(self):
        doc = SourceDocument(
            document_id="doc_v1",
            kb_id="kb_test",
            source_id="src_test",
            source_uri="/path/to/doc.md",
            document_version="v1",
            content="test content",
        )
        chunks = [
            ChunkRecord(
                chunk_id="chunk_1",
                kb_id="kb_test",
                document_id="doc_v1",
                document_version="v1",
                chunk_index=0,
                text="chunk 1",
            ),
            ChunkRecord(
                chunk_id="chunk_2",
                kb_id="kb_test",
                document_id="doc_v1",
                document_version="v1",
                chunk_index=1,
                text="chunk 2",
            ),
        ]
        ingested = IngestedDocument(document=doc, chunks=chunks)
        assert ingested.legacy_chunks() == ["chunk 1", "chunk 2"]


class TestIngestionRunRecord:
    def test_init(self):
        run = IngestionRunRecord(
            run_id="run_001",
            kb_id="kb_test",
            status="completed",
        )
        assert run.run_id == "run_001"
        assert run.kb_id == "kb_test"
        assert run.status == "completed"
        assert run.source_uri == ""
        assert run.error_message == ""
        assert run.documents_seen == 0
        assert run.chunks_written == 0
        assert run.metadata == {}
        assert run.started_at is None
        assert run.finished_at is None
        assert run.updated_at is None
