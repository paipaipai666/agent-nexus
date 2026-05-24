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
            lambda query, limit=10, name=None, namespace=None, where=None: [
                {"id": chunk.chunk_id, "score": 0.9, "text": chunk.text, "metadata": chunk.metadata}
            ],
        )

        result = kb_search("检索用什么", namespace="support")

        assert "docs/support.md" in result
        assert "检索" in result
        assert "BM25" in result

    def test_kb_search_outputs_expanded_context(self, monkeypatch):
        class FakeRetriever:
            def __init__(self, namespace="default"):
                self.namespace = namespace
                self._chunks = {"chunk_1": object()}
                self._reranker = object()

            def rebuild_from_catalog(self):
                return None

            def search(self, query, dense, top_k=5, min_score=0.0, metadata_filters=None):
                from agentnexus.rag.retriever import SearchResult

                return [
                    SearchResult(
                        id="chunk_1",
                        text="核心答案",
                        score=0.88,
                        metadata={"source_uri": "docs/support.md"},
                    )
                ]

            def expand_contexts(self, results, window=None, view="section"):
                results[0].context_text = "前置说明\n\n>> 核心答案\n\n后续补充"
                return results

        monkeypatch.setattr("agentnexus.tools.kb_search.HybridRetriever", FakeRetriever)
        monkeypatch.setattr(
            "agentnexus.tools.kb_search.chroma_search",
            lambda query, limit=10, name=None, namespace=None, where=None: [
                {"id": "chunk_1", "score": 0.9, "text": "核心答案", "metadata": {}}
            ],
        )
        monkeypatch.setattr("agentnexus.tools.kb_search.expand_queries", lambda query: [query])

        result = kb_search("检索用什么", namespace="support")

        assert "docs/support.md" in result
        assert "前置说明" in result
        assert ">> 核心答案" in result
        assert "后续补充" in result

    def test_kb_search_uses_filters_and_reranker(self, monkeypatch):
        captured = {}

        class FakeRetriever:
            def __init__(self, namespace="default"):
                self.namespace = namespace
                self._chunks = {"chunk_1": object()}
                self._reranker = None

            def rebuild_from_catalog(self):
                return None

            def load_reranker(self):
                captured["reranker_loaded"] = True
                self._reranker = object()

            def search(self, query, dense, top_k=5, min_score=0.0, metadata_filters=None):
                return []

        monkeypatch.setattr("agentnexus.tools.kb_search.HybridRetriever", FakeRetriever)

        def fake_chroma_search(query, limit=10, name=None, namespace=None, where=None):
            captured.setdefault("queries", []).append(query)
            captured["where"] = where
            return []

        monkeypatch.setattr("agentnexus.tools.kb_search.chroma_search", fake_chroma_search)
        monkeypatch.setattr(
            "agentnexus.tools.kb_search.expand_queries",
            lambda query: [query, "BM25 查询"],
        )

        result = kb_search(
            "检索用什么",
            namespace="support",
            view="chunk",
            source="docs/support.md",
            file_format="markdown",
            section_title="检索",
            page_number=2,
            block_type="code",
            has_code=True,
            has_list=False,
            heading_depth=2,
        )

        assert result == "[kb_search] 未找到相关知识"
        assert captured["reranker_loaded"] is True
        assert captured["where"] == {
            "source_uri": "docs/support.md",
            "format": "markdown",
            "section_title": "检索",
            "page_number": 2,
            "block_type": "code",
            "has_code": True,
            "has_list": False,
            "heading_depth": 2,
        }
        assert captured["queries"] == ["检索用什么", "BM25 查询"]

    def test_kb_search_passes_chunk_view_to_expansion(self, monkeypatch):
        captured = {}

        class FakeRetriever:
            def __init__(self, namespace="default"):
                self.namespace = namespace
                self._chunks = {"chunk_1": object()}
                self._reranker = object()

            def rebuild_from_catalog(self):
                return None

            def search(self, query, dense, top_k=5, min_score=0.0, metadata_filters=None):
                from agentnexus.rag.retriever import SearchResult

                return [SearchResult(id="chunk_1", text="核心答案", score=0.88, metadata={})]

            def expand_contexts(self, results, window=None, view="section"):
                captured["view"] = view
                return results

        monkeypatch.setattr("agentnexus.tools.kb_search.HybridRetriever", FakeRetriever)
        monkeypatch.setattr(
            "agentnexus.tools.kb_search.chroma_search",
            lambda query, limit=10, name=None, namespace=None, where=None: [
                {"id": "chunk_1", "score": 0.9, "text": "核心答案", "metadata": {}}
            ],
        )
        monkeypatch.setattr("agentnexus.tools.kb_search.expand_queries", lambda query: [query])

        kb_search("检索用什么", namespace="support", view="chunk")

        assert captured["view"] == "chunk"
