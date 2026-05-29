"""E2E tests for RAG pipeline with real embeddings and retrieval.

Exercises: document ingestion → chunking → embedding → ChromaDB storage →
hybrid retrieval (vector + BM25) → reranking → answer generation.
"""

import pytest

from .e2e_helpers import assert_answer_contains_keywords, assert_answer_not_empty


@pytest.mark.e2e
class TestRAGPipeline:
    """Full RAG pipeline: ingest → retrieve → answer."""

    def test_ingest_and_search(self, real_agent, temp_agentnexus_home):
        """Ingest a document and verify retrieval works."""
        from agentnexus.rag.ingestion import ingest_text
        from agentnexus.rag.retriever import HybridRetriever

        doc_text = (
            "AgentNexus 是一个基于 ReAct 模式的单智能体任务协同工具。"
            "它使用 ChromaDB 作为向量数据库，支持 BM25 和向量混合检索。"
            "主要特点包括：工具调用、记忆管理、知识库管理。"
        )
        ingest_text(doc_text, source="test_doc", metadata={"topic": "agentnexus"})

        retriever = HybridRetriever()
        results = retriever.search("AgentNexus 是什么？", top_k=3)

        assert results, "RAG retrieval returned no results"
        texts = [r.get("text", "") for r in results]
        combined = " ".join(texts)
        assert "AgentNexus" in combined or "ReAct" in combined

    def test_rag_answer_quality(self, real_agent, temp_agentnexus_home):
        """Agent generates grounded answer from retrieved context."""
        from agentnexus.rag.ingestion import ingest_text

        doc_text = (
            "Python 是一种解释型、面向对象的高级编程语言。"
            "由 Guido van Rossum 于 1991 年首次发布。"
            "Python 的设计哲学强调代码的可读性和简洁性。"
        )
        ingest_text(doc_text, source="python_doc", metadata={"topic": "python"})

        result = real_agent.run("根据知识库回答：Python 是谁创造的？什么时候发布的？")
        answer = result.answer if hasattr(result, "answer") else str(result)

        assert_answer_not_empty(answer)
        assert_answer_contains_keywords(answer, ["Guido", "1991"], min_hits=1)

    def test_multi_document_retrieval(self, real_agent, temp_agentnexus_home):
        """Retrieval works across multiple documents."""
        from agentnexus.rag.ingestion import ingest_text

        docs = [
            ("ChromaDB 是一个开源向量数据库，专为 AI 应用设计。", "chromadb_doc"),
            ("BM25 是一种基于词频的检索算法，常用于信息检索。", "bm25_doc"),
            ("Reranker 模型用于对检索结果进行重新排序，提高相关性。", "reranker_doc"),
        ]
        for text, source in docs:
            ingest_text(text, source=source, metadata={})

        from agentnexus.rag.retriever import HybridRetriever
        retriever = HybridRetriever()
        results = retriever.search("向量数据库", top_k=5)

        assert len(results) >= 1
        texts = [r.get("text", "") for r in results]
        combined = " ".join(texts)
        assert "ChromaDB" in combined or "向量" in combined
