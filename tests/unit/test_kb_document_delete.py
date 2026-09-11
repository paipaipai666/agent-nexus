"""删除路径的向量泄漏回归：删除文档必须同时清 catalog 和 Chroma 向量。

修复前：API DELETE /documents/{id} 只调 catalog.delete_document，
被删文档的向量永久留在 Chroma（孤儿向量：存储泄漏 + dense 候选窗口污染）。
"""
from unittest.mock import MagicMock, patch

import pytest

from agentnexus.rag.models import ChunkRecord, KnowledgeBaseRecord, SourceDocument


@pytest.fixture()
def catalog(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTNEXUS_HOME", str(tmp_path))
    import agentnexus.core.config as cfg
    cfg._settings_cache = None
    from agentnexus.rag.store import KnowledgeBaseCatalog
    from agentnexus.storage.chroma import resolve_collection_name
    # 生产不变式：kb_id == resolve_collection_name(namespace)
    kb_id = resolve_collection_name(namespace="default")
    cat = KnowledgeBaseCatalog(db_path=str(tmp_path / "kb.db"))
    cat.upsert_knowledge_base(KnowledgeBaseRecord(
        kb_id=kb_id, namespace="default", display_name="d", collection_name=kb_id))
    # kb_service 走全局单例 catalog——指到本夹具实例，隔离真实库
    monkeypatch.setattr(
        "agentnexus.rag.kb_service.get_knowledge_base_catalog", lambda: cat)
    yield cat
    cat.close()


def _seed(cat, doc_id: str, n_chunks: int = 2):
    from agentnexus.storage.chroma import resolve_collection_name
    kb_id = resolve_collection_name(namespace="default")
    cat.upsert_document(SourceDocument(
        document_id=doc_id, kb_id=kb_id, source_id=f"src_{doc_id}",
        source_uri=f"{doc_id}.md", document_version=1, content="内容"))
    cat.upsert_chunks([
        ChunkRecord(chunk_id=f"{doc_id}_ch{i}", kb_id=kb_id, document_id=doc_id,
                    document_version=1, chunk_index=i, text=f"chunk {i}")
        for i in range(n_chunks)
    ])


class TestCatalogDeleteCascade:
    """catalog 层：FK 级联（PRAGMA foreign_keys=ON 在 schema 内）。"""

    def test_delete_document_cascades_chunks(self, catalog):
        _seed(catalog, "doc1", n_chunks=3)
        assert catalog.count_chunks("doc1") == 3
        catalog.delete_document("doc1")
        assert catalog.get_document("doc1") is None
        assert catalog.count_chunks("doc1") == 0, "chunks 必须随 FK 级联删除"


class TestDeleteDocumentAndVectors:
    """完整删除：Chroma 向量 + catalog，双清。"""

    def test_removes_chroma_vectors_and_catalog(self, catalog):
        _seed(catalog, "doc1", n_chunks=2)
        with patch("agentnexus.rag.kb_service.delete_documents") as mock_chroma_del:
            from agentnexus.rag.kb_service import delete_document_and_vectors
            deleted = delete_document_and_vectors("default", "doc1")
        assert deleted == 2
        mock_chroma_del.assert_called_once_with(
            ids=["doc1_ch0", "doc1_ch1"], namespace="default")
        assert catalog.get_document("doc1") is None
        assert catalog.count_chunks("doc1") == 0

    def test_document_without_chunks_skips_chroma(self, catalog):
        _seed(catalog, "doc_empty", n_chunks=0)
        with patch("agentnexus.rag.kb_service.delete_documents") as mock_chroma_del:
            from agentnexus.rag.kb_service import delete_document_and_vectors
            deleted = delete_document_and_vectors("default", "doc_empty")
        assert deleted == 0
        mock_chroma_del.assert_not_called()
        assert catalog.get_document("doc_empty") is None

    def test_nonexistent_document_is_safe_noop(self, catalog):
        with patch("agentnexus.rag.kb_service.delete_documents") as mock_chroma_del:
            from agentnexus.rag.kb_service import delete_document_and_vectors
            deleted = delete_document_and_vectors("default", "ghost_doc")
        assert deleted == 0
        mock_chroma_del.assert_not_called()

    def test_reingest_path_still_consistent(self, catalog):
        """重摄入（更新）路径的删除语义不变：同来源旧版本向量+文档全清。"""
        _seed(catalog, "doc_v1", n_chunks=2)
        with patch("agentnexus.rag.kb_service.delete_documents") as mock_chroma_del:
            from agentnexus.rag.kb_service import delete_existing_source_versions
            removed = delete_existing_source_versions("default", "src_doc_v1")
        assert removed == 2
        mock_chroma_del.assert_called_once_with(
            ids=["doc_v1_ch0", "doc_v1_ch1"], namespace="default")
        assert catalog.get_document("doc_v1") is None


class TestDeleteRoute:
    """路由层：走完整删除并返回向量删除数。"""

    def test_route_calls_full_delete(self, monkeypatch):
        mock_settings = MagicMock()
        mock_settings.rag_default_namespace = "default"
        monkeypatch.setattr("agentnexus.core.config.get_settings",
                            lambda: mock_settings)
        with patch("agentnexus.rag.kb_service.delete_document_and_vectors",
                   return_value=3) as mock_full:
            from agentnexus.server.routes.knowledge import delete_document
            result = delete_document("doc1")
        mock_full.assert_called_once_with("default", "doc1")
        assert result == {"status": "deleted", "doc_id": "doc1", "deleted_vectors": 3}
