from agentnexus.rag.evaluator import EvalSample, RAGEvaluator
from agentnexus.rag.ids import make_chunk_id, make_document_version, make_source_id
from agentnexus.rag.models import ChunkRecord, KnowledgeBaseRecord, SourceDocument
from agentnexus.rag.retriever import (
    HybridRetriever,
    build_knowledge_base,
    expand_queries,
    generate_hypothetical_document,
    reciprocal_rank_fusion,
    result_citation,
    result_display_text,
    rewrite_query,
    search_knowledge_base,
)
from agentnexus.rag.store import KnowledgeBaseCatalog


class TestChunkIdFusion:
    def test_rrf_fuses_by_chunk_id_not_position(self):
        dense = [("chunk_b", 0.9), ("chunk_a", 0.8)]
        sparse = [("chunk_a", 12.0)]

        fused = reciprocal_rank_fusion(dense, sparse, k=60)

        assert fused["chunk_a"] > fused["chunk_b"]


class TestQueryExpansion:
    def test_rewrite_query_returns_original_on_failure(self, monkeypatch):
        class FakeLLM:
            def think(self, *args, **kwargs):
                raise RuntimeError("boom")

        monkeypatch.setattr("agentnexus.rag.retriever.AgentLLM", lambda: FakeLLM())
        assert rewrite_query("原始问题") == "原始问题"

    def test_expand_queries_dedupes_and_keeps_original(self, monkeypatch):
        responses = iter(["优化后的查询", "优化后的查询\n原始问题\n另一个问法"])

        class FakeLLM:
            def think(self, *args, **kwargs):
                return next(responses)

        monkeypatch.setattr("agentnexus.rag.retriever.AgentLLM", lambda: FakeLLM())
        queries = expand_queries("原始问题")

        assert queries[0] == "原始问题"
        assert "优化后的查询" in queries
        assert "另一个问法" in queries
        assert len(queries) == len(set(queries))

    def test_generate_hypothetical_document_returns_empty_when_disabled(self):
        assert generate_hypothetical_document("什么是 BM25") == ""

    def test_generate_hypothetical_document_question_only_guard(self, monkeypatch):
        import agentnexus.rag.retriever as retriever_mod

        original_get_settings = retriever_mod.get_settings

        def fake_settings():
            settings = original_get_settings()
            settings.enable_hyde = True
            settings.hyde_question_only = True
            return settings

        monkeypatch.setattr(retriever_mod, "get_settings", fake_settings)

        assert generate_hypothetical_document("BM25 检索实现") == ""

    def test_generate_hypothetical_document_uses_llm(self, monkeypatch):
        import agentnexus.rag.retriever as retriever_mod

        original_get_settings = retriever_mod.get_settings

        def fake_settings():
            settings = original_get_settings()
            settings.enable_hyde = True
            settings.hyde_question_only = False
            return settings

        class FakeLLM:
            def think(self, *args, **kwargs):
                return "BM25 是一种稀疏检索算法。"

        monkeypatch.setattr(retriever_mod, "get_settings", fake_settings)
        monkeypatch.setattr(retriever_mod, "AgentLLM", lambda: FakeLLM())

        assert "BM25" in generate_hypothetical_document("什么是 BM25")


class TestRestartSafeRetrieval:
    def _seed_catalog(self):
        catalog = KnowledgeBaseCatalog()
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
            kb_id="kb_support",
            source_id=source_id,
            source_uri="docs/support.md",
            document_version=document_version,
            content="BM25 文本检索",
        )
        catalog.upsert_document(document)
        chunk = ChunkRecord(
            chunk_id=make_chunk_id(document_version, 0, "BM25 文本检索"),
            kb_id="kb_support",
            document_id=document.document_id,
            document_version=document_version,
            chunk_index=0,
            text="BM25 文本检索",
            indexed_text="BM25 文本检索",
            sparse_text="BM25 文本检索",
            metadata={"source_uri": "docs/support.md"},
        )
        catalog.upsert_chunks([chunk])
        return chunk

    def test_hybrid_retriever_rebuilds_from_catalog_chunks(self, temp_agentnexus_home):
        chunk = self._seed_catalog()

        retriever = HybridRetriever(namespace="support")
        retriever.rebuild_from_catalog()

        results = retriever.search(
            query="检索用什么",
            dense_results=[(chunk.chunk_id, 0.2)],
            top_k=5,
            min_score=0.0,
        )

        assert results
        assert results[0].id == chunk.chunk_id
        assert "BM25" in results[0].text

    def test_search_knowledge_base_works_after_retriever_reset(self, temp_agentnexus_home, monkeypatch):
        chunk = self._seed_catalog()

        monkeypatch.setattr("agentnexus.rag.retriever.expand_queries", lambda query: [query])
        monkeypatch.setattr(
            "agentnexus.rag.retriever.chroma_search",
            lambda query, limit=10, name=None, namespace=None: [
                {"id": chunk.chunk_id, "score": 0.9, "text": chunk.text, "metadata": {"source_uri": "docs/support.md"}}
            ],
        )

        import agentnexus.rag.retriever as retriever_mod
        retriever_mod._retriever = None

        result = search_knowledge_base("检索用什么", namespace="support")

        assert "BM25" in result
        assert "docs/support.md" in result

    def test_search_knowledge_base_loads_reranker_by_default(self, temp_agentnexus_home, monkeypatch):
        chunk = self._seed_catalog()

        monkeypatch.setattr("agentnexus.rag.retriever.expand_queries", lambda query: [query])
        monkeypatch.setattr(
            "agentnexus.rag.retriever.chroma_search",
            lambda query, limit=10, name=None, namespace=None: [
                {"id": chunk.chunk_id, "score": 0.9, "text": chunk.text, "metadata": {"source_uri": "docs/support.md"}}
            ],
        )

        calls = {"loaded": 0}
        original_search = HybridRetriever.search

        def fake_load_reranker(self, model_name=None):
            calls["loaded"] += 1
            self._reranker = object()

        def fake_search(self, query, dense_results, top_k=5, rrf_k=60, min_score=0.0):
            return original_search(self, query, dense_results, top_k=top_k, rrf_k=rrf_k, min_score=min_score)

        monkeypatch.setattr(HybridRetriever, "load_reranker", fake_load_reranker)
        monkeypatch.setattr(HybridRetriever, "search", fake_search)

        import agentnexus.rag.retriever as retriever_mod
        retriever_mod._retriever = HybridRetriever(namespace="support")
        retriever_mod._retriever.rebuild_from_catalog()

        result = search_knowledge_base("检索用什么", namespace="support")

        assert "BM25" in result
        assert calls["loaded"] == 1

    def test_search_knowledge_base_fuses_multiple_queries(self, temp_agentnexus_home, monkeypatch):
        chunk = self._seed_catalog()
        captured_queries = []

        monkeypatch.setattr("agentnexus.rag.retriever.expand_queries", lambda query: [query, "BM25 查询"])

        def fake_search(query, limit=10, name=None, namespace=None):
            captured_queries.append(query)
            return [{"id": chunk.chunk_id, "score": 0.9, "text": chunk.text, "metadata": chunk.metadata}]

        monkeypatch.setattr("agentnexus.rag.retriever.chroma_search", fake_search)

        import agentnexus.rag.retriever as retriever_mod
        retriever_mod._retriever = None

        result = search_knowledge_base("检索用什么", namespace="support")

        assert "BM25" in result
        assert captured_queries == ["检索用什么", "BM25 查询"]

    def test_search_knowledge_base_adds_hyde_dense_query(self, temp_agentnexus_home, monkeypatch):
        chunk = self._seed_catalog()
        captured_queries = []

        monkeypatch.setattr("agentnexus.rag.retriever.expand_queries", lambda query: [query])
        monkeypatch.setattr(
            "agentnexus.rag.retriever.generate_hypothetical_document",
            lambda query: "BM25 是稀疏检索算法",
        )

        def fake_search(query, limit=10, name=None, namespace=None):
            captured_queries.append(query)
            return [{"id": chunk.chunk_id, "score": 0.9, "text": chunk.text, "metadata": chunk.metadata}]

        monkeypatch.setattr("agentnexus.rag.retriever.chroma_search", fake_search)

        import agentnexus.rag.retriever as retriever_mod
        retriever_mod._retriever = None

        result = search_knowledge_base("检索用什么", namespace="support")

        assert "BM25" in result
        assert captured_queries == ["检索用什么", "BM25 是稀疏检索算法"]

    def test_build_knowledge_base_replaces_old_chunks_in_namespace(self, temp_agentnexus_home):
        from unittest.mock import patch

        with patch("agentnexus.rag.retriever.insert_documents", lambda *args, **kwargs: None):
            build_knowledge_base(["doc A", "stale B"], load_reranker=False, namespace="support")
            build_knowledge_base(["doc A"], load_reranker=False, namespace="support")

        catalog = KnowledgeBaseCatalog()
        kb = catalog.get_knowledge_base("support")
        assert kb is not None
        chunks = catalog.list_chunks_by_kb(kb.kb_id)
        assert [chunk.text for chunk in chunks] == ["doc A"]

    def test_evaluator_hybrid_path_uses_real_chunk_ids(self, monkeypatch):
        sample = EvalSample(question="检索用什么", ground_truth="BM25", reference_contexts=["BM25 文本检索"])
        evaluator = RAGEvaluator(["BM25 文本检索"], [sample])

        captured = {}

        monkeypatch.setattr(
            "agentnexus.rag.evaluator.search",
            lambda query, limit=20, name=None, namespace=None: [
                {"id": "chunk_real_id", "score": 0.9, "text": "BM25 文本检索", "metadata": {}}
            ],
        )

        class FakeRetriever:
            def search(self, query, dense_results, top_k=10, min_score=0.3):
                captured["dense_results"] = dense_results
                return []

        full_ranked, truncated = evaluator._retrieve(
            query="检索用什么",
            retriever=FakeRetriever(),
            use_hybrid=True,
            max_tokens=100,
        )

        assert full_ranked == []
        assert truncated == []
        assert captured["dense_results"] == [("chunk_real_id", 0.9)]


class TestStructuralRetrieval:
    def test_bm25_search_respects_metadata_filters(self):
        retriever = HybridRetriever(namespace="support")
        source_id = make_source_id("docs/guide.md")
        doc_version = make_document_version(source_id, "doc body")
        document = SourceDocument(
            document_id=doc_version,
            kb_id="kb_support",
            source_id=source_id,
            source_uri="docs/guide.md",
            document_version=doc_version,
            content="doc body",
        )
        code_chunk = ChunkRecord(
            chunk_id=make_chunk_id(doc_version, 0, "def search(): pass"),
            kb_id="kb_support",
            document_id=document.document_id,
            document_version=document.document_version,
            chunk_index=0,
            text="def search(): pass",
            indexed_text="def search(): pass",
            sparse_text="def search(): pass",
            metadata={"source_uri": "docs/guide.md", "block_type": "code", "has_code": True},
        )
        paragraph_chunk = ChunkRecord(
            chunk_id=make_chunk_id(doc_version, 1, "搜索实现说明"),
            kb_id="kb_support",
            document_id=document.document_id,
            document_version=document.document_version,
            chunk_index=1,
            text="搜索实现说明",
            indexed_text="搜索实现说明",
            sparse_text="搜索实现说明",
            metadata={"source_uri": "docs/guide.md", "block_type": "paragraph", "has_code": False},
        )

        retriever._chunks = {
            code_chunk.chunk_id: code_chunk,
            paragraph_chunk.chunk_id: paragraph_chunk,
        }
        retriever._bm25.build([code_chunk, paragraph_chunk])

        results = retriever.search(
            query="搜索实现代码",
            dense_results=[
                (code_chunk.chunk_id, 0.2),
                (paragraph_chunk.chunk_id, 0.19),
            ],
            top_k=5,
            min_score=0.0,
            metadata_filters={"block_type": "code", "has_code": True},
        )

        assert [item.id for item in results] == [code_chunk.chunk_id]

    def test_structural_boost_prefers_code_for_code_query(self):
        retriever = HybridRetriever(namespace="support")
        source_id = make_source_id("docs/guide.md")
        doc_version = make_document_version(source_id, "doc body")
        document = SourceDocument(
            document_id=doc_version,
            kb_id="kb_support",
            source_id=source_id,
            source_uri="docs/guide.md",
            document_version=doc_version,
            content="doc body",
        )
        code_chunk = ChunkRecord(
            chunk_id=make_chunk_id(doc_version, 0, "def retrieve(): pass"),
            kb_id="kb_support",
            document_id=document.document_id,
            document_version=document.document_version,
            chunk_index=0,
            text="def retrieve(): pass",
            indexed_text="def retrieve(): pass",
            sparse_text="def retrieve(): pass",
            metadata={"source_uri": "docs/guide.md", "block_type": "code", "has_code": True},
        )
        paragraph_chunk = ChunkRecord(
            chunk_id=make_chunk_id(doc_version, 1, "这里介绍检索原理"),
            kb_id="kb_support",
            document_id=document.document_id,
            document_version=document.document_version,
            chunk_index=1,
            text="这里介绍检索原理",
            indexed_text="这里介绍检索原理",
            sparse_text="这里介绍检索原理",
            metadata={"source_uri": "docs/guide.md", "block_type": "paragraph", "has_code": False},
        )

        retriever._chunks = {
            code_chunk.chunk_id: code_chunk,
            paragraph_chunk.chunk_id: paragraph_chunk,
        }
        retriever._bm25.build([code_chunk, paragraph_chunk])

        results = retriever.search(
            query="给我检索代码示例",
            dense_results=[
                (paragraph_chunk.chunk_id, 0.2),
                (code_chunk.chunk_id, 0.2),
            ],
            top_k=2,
            min_score=0.0,
        )

        assert results
        assert results[0].id == code_chunk.chunk_id

    def test_expand_contexts_includes_neighbor_chunks(self, temp_agentnexus_home):
        catalog = KnowledgeBaseCatalog()
        kb = KnowledgeBaseRecord(
            kb_id="kb_support",
            namespace="support",
            display_name="Support KB",
            collection_name="kb_support",
        )
        catalog.upsert_knowledge_base(kb)

        source_id = make_source_id("docs/guide.md")
        doc_version = make_document_version(source_id, "doc body")
        document = SourceDocument(
            document_id=doc_version,
            kb_id=kb.kb_id,
            source_id=source_id,
            source_uri="docs/guide.md",
            document_version=doc_version,
            content="doc body",
        )
        catalog.upsert_document(document)
        chunks = [
            ChunkRecord(
                chunk_id=make_chunk_id(doc_version, 0, "前置说明"),
                kb_id=kb.kb_id,
                document_id=document.document_id,
                document_version=document.document_version,
                chunk_index=0,
                text="前置说明",
                indexed_text="前置说明",
                sparse_text="前置说明",
                metadata={"source_uri": "docs/guide.md"},
            ),
            ChunkRecord(
                chunk_id=make_chunk_id(doc_version, 1, "核心答案"),
                kb_id=kb.kb_id,
                document_id=document.document_id,
                document_version=document.document_version,
                chunk_index=1,
                text="核心答案",
                indexed_text="核心答案",
                sparse_text="核心答案",
                metadata={"source_uri": "docs/guide.md"},
            ),
            ChunkRecord(
                chunk_id=make_chunk_id(doc_version, 2, "后续补充"),
                kb_id=kb.kb_id,
                document_id=document.document_id,
                document_version=document.document_version,
                chunk_index=2,
                text="后续补充",
                indexed_text="后续补充",
                sparse_text="后续补充",
                metadata={"source_uri": "docs/guide.md"},
            ),
        ]
        catalog.upsert_chunks(chunks)

        retriever = HybridRetriever(namespace="support")
        retriever.rebuild_from_catalog()
        expanded = retriever.expand_contexts(
            [retriever.search("核心答案", [(chunks[1].chunk_id, 0.2)], top_k=1, min_score=0.0)[0]],
            window=1,
        )

        assert expanded[0].context_text is not None
        assert "前置说明" in expanded[0].context_text
        assert "核心答案" in expanded[0].context_text
        assert "后续补充" in expanded[0].context_text
        assert ">> 核心答案" in expanded[0].context_text

    def test_expand_contexts_prefers_same_section_chunks(self, temp_agentnexus_home):
        catalog = KnowledgeBaseCatalog()
        kb = KnowledgeBaseRecord(
            kb_id="kb_support",
            namespace="support",
            display_name="Support KB",
            collection_name="kb_support",
        )
        catalog.upsert_knowledge_base(kb)

        source_id = make_source_id("docs/guide.md")
        doc_version = make_document_version(source_id, "doc body")
        document = SourceDocument(
            document_id=doc_version,
            kb_id=kb.kb_id,
            source_id=source_id,
            source_uri="docs/guide.md",
            document_version=doc_version,
            content="doc body",
        )
        catalog.upsert_document(document)
        chunks = [
            ChunkRecord(
                chunk_id=make_chunk_id(doc_version, 0, "第一节-前置", {"section_index": 0}),
                kb_id=kb.kb_id,
                document_id=document.document_id,
                document_version=document.document_version,
                chunk_index=0,
                text="第一节-前置",
                indexed_text="第一节-前置",
                sparse_text="第一节-前置",
                metadata={"source_uri": "docs/guide.md", "section_index": 0},
            ),
            ChunkRecord(
                chunk_id=make_chunk_id(doc_version, 1, "第一节-核心", {"section_index": 0}),
                kb_id=kb.kb_id,
                document_id=document.document_id,
                document_version=document.document_version,
                chunk_index=1,
                text="第一节-核心",
                indexed_text="第一节-核心",
                sparse_text="第一节-核心",
                metadata={"source_uri": "docs/guide.md", "section_index": 0},
            ),
            ChunkRecord(
                chunk_id=make_chunk_id(doc_version, 2, "第二节-内容", {"section_index": 1}),
                kb_id=kb.kb_id,
                document_id=document.document_id,
                document_version=document.document_version,
                chunk_index=2,
                text="第二节-内容",
                indexed_text="第二节-内容",
                sparse_text="第二节-内容",
                metadata={"source_uri": "docs/guide.md", "section_index": 1},
            ),
        ]
        catalog.upsert_chunks(chunks)

        retriever = HybridRetriever(namespace="support")
        retriever.rebuild_from_catalog()
        expanded = retriever.expand_contexts(
            [retriever.search("核心", [(chunks[1].chunk_id, 0.2)], top_k=1, min_score=0.0)[0]],
            window=1,
        )

        assert expanded[0].context_text is not None
        assert "第一节-前置" in expanded[0].context_text
        assert ">> 第一节-核心" in expanded[0].context_text
        assert "第二节-内容" not in expanded[0].context_text

    def test_expand_contexts_respects_max_chunk_limit(self, temp_agentnexus_home, monkeypatch):
        catalog = KnowledgeBaseCatalog()
        kb = KnowledgeBaseRecord(
            kb_id="kb_support",
            namespace="support",
            display_name="Support KB",
            collection_name="kb_support",
        )
        catalog.upsert_knowledge_base(kb)

        source_id = make_source_id("docs/guide.md")
        doc_version = make_document_version(source_id, "doc body")
        document = SourceDocument(
            document_id=doc_version,
            kb_id=kb.kb_id,
            source_id=source_id,
            source_uri="docs/guide.md",
            document_version=doc_version,
            content="doc body",
        )
        catalog.upsert_document(document)
        chunks = []
        for index in range(4):
            chunks.append(
                ChunkRecord(
                    chunk_id=make_chunk_id(doc_version, index, f"段落{index}", {"section_index": 0}),
                    kb_id=kb.kb_id,
                    document_id=document.document_id,
                    document_version=document.document_version,
                    chunk_index=index,
                    text=f"段落{index}",
                    indexed_text=f"段落{index}",
                    sparse_text=f"段落{index}",
                    metadata={"source_uri": "docs/guide.md", "section_index": 0},
                )
            )
        catalog.upsert_chunks(chunks)

        import agentnexus.rag.retriever as retriever_mod

        original_get_settings = retriever_mod.get_settings

        def fake_settings():
            settings = original_get_settings()
            settings.rag_context_max_chunks = 2
            return settings

        monkeypatch.setattr(retriever_mod, "get_settings", fake_settings)

        retriever = HybridRetriever(namespace="support")
        retriever.rebuild_from_catalog()
        expanded = retriever.expand_contexts(
            [retriever.search("段落1", [(chunks[1].chunk_id, 0.2)], top_k=1, min_score=0.0)[0]],
            window=1,
        )

        assert expanded[0].context_text is not None
        assert expanded[0].context_text.count("段落") == 2

    def test_result_display_text_falls_back_to_text(self):
        from agentnexus.rag.retriever import SearchResult

        result = SearchResult(id="c1", text="正文", score=0.8, context_text=None)

        assert result_display_text(result) == "正文"

    def test_result_citation_uses_metadata(self):
        from agentnexus.rag.retriever import SearchResult

        result = SearchResult(
            id="chunk_1",
            text="正文",
            score=0.8,
            metadata={
                "source_uri": "docs/guide.md",
                "section_title": "安装",
                "page_number": 3,
                "heading_depth": 2,
            },
        )

        assert result_citation(result) == "docs/guide.md [安装 | Page 3 | H2]"

    def test_merge_results_by_section_dedupes_same_section_hits(self):
        retriever = HybridRetriever(namespace="support")
        source_id = make_source_id("docs/guide.md")
        doc_version = make_document_version(source_id, "doc body")
        document = SourceDocument(
            document_id=doc_version,
            kb_id="kb_support",
            source_id=source_id,
            source_uri="docs/guide.md",
            document_version=doc_version,
            content="doc body",
        )
        chunk_a = ChunkRecord(
            chunk_id=make_chunk_id(doc_version, 0, "第一节-片段A", {"section_index": 0}),
            kb_id="kb_support",
            document_id=document.document_id,
            document_version=document.document_version,
            chunk_index=0,
            text="第一节-片段A",
            indexed_text="第一节-片段A",
            sparse_text="第一节-片段A",
            metadata={"section_index": 0},
        )
        chunk_b = ChunkRecord(
            chunk_id=make_chunk_id(doc_version, 1, "第一节-片段B", {"section_index": 0}),
            kb_id="kb_support",
            document_id=document.document_id,
            document_version=document.document_version,
            chunk_index=1,
            text="第一节-片段B",
            indexed_text="第一节-片段B",
            sparse_text="第一节-片段B",
            metadata={"section_index": 0},
        )
        chunk_c = ChunkRecord(
            chunk_id=make_chunk_id(doc_version, 2, "第二节-片段", {"section_index": 1}),
            kb_id="kb_support",
            document_id=document.document_id,
            document_version=document.document_version,
            chunk_index=2,
            text="第二节-片段",
            indexed_text="第二节-片段",
            sparse_text="第二节-片段",
            metadata={"section_index": 1},
        )
        retriever._chunks = {
            chunk_a.chunk_id: chunk_a,
            chunk_b.chunk_id: chunk_b,
            chunk_c.chunk_id: chunk_c,
        }

        from agentnexus.rag.retriever import SearchResult

        merged = retriever.merge_results_by_section(
            [
                SearchResult(id=chunk_a.chunk_id, text=chunk_a.text, score=0.9),
                SearchResult(id=chunk_b.chunk_id, text=chunk_b.text, score=0.8),
                SearchResult(id=chunk_c.chunk_id, text=chunk_c.text, score=0.7),
            ]
        )

        assert [item.id for item in merged] == [chunk_a.chunk_id, chunk_c.chunk_id]

    def test_expand_contexts_dedupes_same_section_before_expansion(self, temp_agentnexus_home):
        catalog = KnowledgeBaseCatalog()
        kb = KnowledgeBaseRecord(
            kb_id="kb_support",
            namespace="support",
            display_name="Support KB",
            collection_name="kb_support",
        )
        catalog.upsert_knowledge_base(kb)

        source_id = make_source_id("docs/guide.md")
        doc_version = make_document_version(source_id, "doc body")
        document = SourceDocument(
            document_id=doc_version,
            kb_id=kb.kb_id,
            source_id=source_id,
            source_uri="docs/guide.md",
            document_version=doc_version,
            content="doc body",
        )
        catalog.upsert_document(document)
        chunks = [
            ChunkRecord(
                chunk_id=make_chunk_id(doc_version, 0, "第一节-前置", {"section_index": 0}),
                kb_id=kb.kb_id,
                document_id=document.document_id,
                document_version=document.document_version,
                chunk_index=0,
                text="第一节-前置",
                indexed_text="第一节-前置",
                sparse_text="第一节-前置",
                metadata={"source_uri": "docs/guide.md", "section_index": 0},
            ),
            ChunkRecord(
                chunk_id=make_chunk_id(doc_version, 1, "第一节-核心", {"section_index": 0}),
                kb_id=kb.kb_id,
                document_id=document.document_id,
                document_version=document.document_version,
                chunk_index=1,
                text="第一节-核心",
                indexed_text="第一节-核心",
                sparse_text="第一节-核心",
                metadata={"source_uri": "docs/guide.md", "section_index": 0},
            ),
            ChunkRecord(
                chunk_id=make_chunk_id(doc_version, 2, "第二节-内容", {"section_index": 1}),
                kb_id=kb.kb_id,
                document_id=document.document_id,
                document_version=document.document_version,
                chunk_index=2,
                text="第二节-内容",
                indexed_text="第二节-内容",
                sparse_text="第二节-内容",
                metadata={"source_uri": "docs/guide.md", "section_index": 1},
            ),
        ]
        catalog.upsert_chunks(chunks)

        retriever = HybridRetriever(namespace="support")
        retriever.rebuild_from_catalog()

        from agentnexus.rag.retriever import SearchResult

        expanded = retriever.expand_contexts(
            [
                SearchResult(id=chunks[1].chunk_id, text=chunks[1].text, score=0.9),
                SearchResult(id=chunks[0].chunk_id, text=chunks[0].text, score=0.85),
                SearchResult(id=chunks[2].chunk_id, text=chunks[2].text, score=0.8),
            ],
            window=1,
        )

        assert len(expanded) == 2
        assert "第一节-前置" in expanded[0].context_text
        assert ">> 第一节-核心" in expanded[0].context_text or ">> 第一节-前置" in expanded[0].context_text

    def test_expand_contexts_chunk_view_keeps_same_section_hits(self, temp_agentnexus_home):
        catalog = KnowledgeBaseCatalog()
        kb = KnowledgeBaseRecord(
            kb_id="kb_support",
            namespace="support",
            display_name="Support KB",
            collection_name="kb_support",
        )
        catalog.upsert_knowledge_base(kb)

        source_id = make_source_id("docs/guide.md")
        doc_version = make_document_version(source_id, "doc body")
        document = SourceDocument(
            document_id=doc_version,
            kb_id=kb.kb_id,
            source_id=source_id,
            source_uri="docs/guide.md",
            document_version=doc_version,
            content="doc body",
        )
        catalog.upsert_document(document)
        chunks = [
            ChunkRecord(
                chunk_id=make_chunk_id(doc_version, 0, "第一节-前置", {"section_index": 0}),
                kb_id=kb.kb_id,
                document_id=document.document_id,
                document_version=document.document_version,
                chunk_index=0,
                text="第一节-前置",
                indexed_text="第一节-前置",
                sparse_text="第一节-前置",
                metadata={"source_uri": "docs/guide.md", "section_index": 0},
            ),
            ChunkRecord(
                chunk_id=make_chunk_id(doc_version, 1, "第一节-核心", {"section_index": 0}),
                kb_id=kb.kb_id,
                document_id=document.document_id,
                document_version=document.document_version,
                chunk_index=1,
                text="第一节-核心",
                indexed_text="第一节-核心",
                sparse_text="第一节-核心",
                metadata={"source_uri": "docs/guide.md", "section_index": 0},
            ),
        ]
        catalog.upsert_chunks(chunks)

        retriever = HybridRetriever(namespace="support")
        retriever.rebuild_from_catalog()

        from agentnexus.rag.retriever import SearchResult

        expanded = retriever.expand_contexts(
            [
                SearchResult(id=chunks[1].chunk_id, text=chunks[1].text, score=0.9),
                SearchResult(id=chunks[0].chunk_id, text=chunks[0].text, score=0.85),
            ],
            window=1,
            view="chunk",
        )

        assert len(expanded) == 2

    def test_expand_contexts_populates_citation(self, temp_agentnexus_home):
        catalog = KnowledgeBaseCatalog()
        kb = KnowledgeBaseRecord(
            kb_id="kb_support",
            namespace="support",
            display_name="Support KB",
            collection_name="kb_support",
        )
        catalog.upsert_knowledge_base(kb)

        source_id = make_source_id("docs/guide.md")
        doc_version = make_document_version(source_id, "doc body")
        document = SourceDocument(
            document_id=doc_version,
            kb_id=kb.kb_id,
            source_id=source_id,
            source_uri="docs/guide.md",
            document_version=doc_version,
            content="doc body",
        )
        catalog.upsert_document(document)
        chunk = ChunkRecord(
            chunk_id=make_chunk_id(doc_version, 0, "核心答案", {"section_index": 0}),
            kb_id=kb.kb_id,
            document_id=document.document_id,
            document_version=document.document_version,
            chunk_index=0,
            text="核心答案",
            indexed_text="核心答案",
            sparse_text="核心答案",
            metadata={
                "source_uri": "docs/guide.md",
                "section_index": 0,
                "section_title": "安装",
                "heading_depth": 2,
                "page_number": 3,
            },
            page_number=3,
        )
        catalog.upsert_chunks([chunk])

        retriever = HybridRetriever(namespace="support")
        retriever.rebuild_from_catalog()
        expanded = retriever.expand_contexts(
            [retriever.search("核心答案", [(chunk.chunk_id, 0.2)], top_k=1, min_score=0.0)[0]],
            window=1,
        )

        assert expanded[0].citation == "docs/guide.md [安装 | Page 3 | H2]"
