from agentnexus.rag.evaluator import EvalSample, RAGEvaluator
from agentnexus.rag.ids import make_chunk_id, make_document_version, make_source_id
from agentnexus.rag.models import ChunkRecord, KnowledgeBaseRecord, SourceDocument
from agentnexus.rag.retriever import (
    HybridRetriever,
    build_knowledge_base,
    reciprocal_rank_fusion,
    search_knowledge_base,
)
from agentnexus.rag.store import KnowledgeBaseCatalog


class TestChunkIdFusion:
    def test_rrf_fuses_by_chunk_id_not_position(self):
        dense = [("chunk_b", 0.9), ("chunk_a", 0.8)]
        sparse = [("chunk_a", 12.0)]

        fused = reciprocal_rank_fusion(dense, sparse, k=60)

        assert fused["chunk_a"] > fused["chunk_b"]


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
        assert chunk.chunk_id in result

    def test_build_knowledge_base_replaces_old_chunks_in_namespace(self, temp_agentnexus_home):
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

        results = evaluator._retrieve(
            query="检索用什么",
            retriever=FakeRetriever(),
            use_hybrid=True,
            max_tokens=100,
        )

        assert results == []
        assert captured["dense_results"] == [("chunk_real_id", 0.9)]
