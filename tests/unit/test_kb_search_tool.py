from agentnexus.rag.ids import make_chunk_id, make_document_version, make_source_id
from agentnexus.rag.models import ChunkRecord, KnowledgeBaseRecord, SourceDocument
from agentnexus.rag.store import get_knowledge_base_catalog
from agentnexus.tools.kb_search import kb_search


class TestKbSearchTool:
    def test_kb_search_returns_cited_chunk_results(self, temp_agentnexus_home, monkeypatch):
        catalog = get_knowledge_base_catalog()
        kb = KnowledgeBaseRecord(
            kb_id="kb_support",
            namespace="support",
            display_name="Support KB",
            collection_name="kb_support",
        )
        catalog.upsert_knowledge_base(kb)

        source_id = make_source_id("docs/support.md")
        document_version = make_document_version(source_id, "BM25 文本检索")
        document = SourceDocument(
            document_id=document_version,
            kb_id=kb.kb_id,
            source_id=source_id,
            source_uri="docs/support.md",
            document_version=document_version,
            content="BM25 文本检索",
        )
        catalog.upsert_document(document)
        chunk = ChunkRecord(
            chunk_id=make_chunk_id(document_version, 0, "BM25 文本检索"),
            kb_id=kb.kb_id,
            document_id=document.document_id,
            document_version=document_version,
            chunk_index=0,
            text="BM25 文本检索",
            indexed_text="BM25 文本检索",
            sparse_text="BM25 文本检索",
            metadata={"source_uri": "docs/support.md", "section_title": "检索"},
        )
        catalog.upsert_chunks([chunk])

        monkeypatch.setattr(
            "agentnexus.tools.kb_search.chroma_search",
            lambda query, limit=10, name=None, namespace=None: [
                {"id": chunk.chunk_id, "score": 0.9, "text": chunk.text, "metadata": chunk.metadata}
            ],
        )

        result = kb_search("检索用什么", namespace="support")

        assert chunk.chunk_id in result
        assert "docs/support.md" in result
        assert "BM25" in result
