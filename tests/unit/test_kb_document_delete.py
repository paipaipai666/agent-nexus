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


class TestDeleteFailureModes:
    """双写一致性：Chroma-first 顺序在各失败点下的实际终态。

    设计现实（代码核实）：删除顺序是 先 Chroma 后 catalog，
    无 embedding_synced 式补偿标记、无对账任务——一致性依赖
    「异常可见 + 重试幂等收敛」。
    """

    def test_chroma_failure_aborts_before_catalog_touch(self, catalog):
        """Chroma 删除抛错 → catalog 不动 → 两侧都在 → 可安全重试。"""
        _seed(catalog, "doc1", n_chunks=2)
        with patch("agentnexus.rag.kb_service.delete_documents",
                   side_effect=RuntimeError("chroma down")):
            from agentnexus.rag.kb_service import delete_document_and_vectors
            with pytest.raises(RuntimeError, match="chroma down"):
                delete_document_and_vectors("default", "doc1")
        assert catalog.get_document("doc1") is not None
        assert catalog.count_chunks("doc1") == 2, "Chroma 失败时 catalog 必须原样保留"

    def test_catalog_failure_after_chroma_success_then_retry_heals(self, catalog, monkeypatch):
        """Chroma 成功 + catalog 抛错 → 中间态（向量已删/目录还在）→ 重试幂等收敛。"""
        _seed(catalog, "doc1", n_chunks=2)
        monkeypatch.setattr("agentnexus.rag.kb_service.time.sleep", lambda s: None)
        chroma_calls: list[list[str]] = []

        def fake_chroma_delete(ids=None, where=None, namespace=None, **kw):
            chroma_calls.append(list(ids or []))

        with patch("agentnexus.rag.kb_service.delete_documents",
                   side_effect=fake_chroma_delete):
            with patch.object(catalog, "delete_document",
                              side_effect=RuntimeError("db locked")):
                from agentnexus.rag.kb_service import (
                    DeleteIncompleteError,
                    delete_document_and_vectors,
                )
                with pytest.raises(DeleteIncompleteError):
                    delete_document_and_vectors("default", "doc1")

        # 中间态确认：向量已删，catalog 还在（文档仍列出，但 dense 路已搜不到）
        assert chroma_calls == [["doc1_ch0", "doc1_ch1"]]
        assert catalog.get_document("doc1") is not None

        # 重试同一操作：Chroma 幂等重删无副作用，catalog 这次成功 → 终态一致
        with patch("agentnexus.rag.kb_service.delete_documents",
                   side_effect=fake_chroma_delete):
            deleted = delete_document_and_vectors("default", "doc1")
        assert deleted == 2
        assert catalog.get_document("doc1") is None
        assert catalog.count_chunks("doc1") == 0

    def test_reingest_path_chroma_failure_keeps_old_version(self, catalog):
        """重摄入路径：旧版本 Chroma 删除失败 → 旧版本 catalog 不动，新版本未写入。"""
        _seed(catalog, "doc_v1", n_chunks=2)
        with patch("agentnexus.rag.kb_service.delete_documents",
                   side_effect=RuntimeError("chroma down")):
            from agentnexus.rag.kb_service import delete_existing_source_versions
            with pytest.raises(RuntimeError, match="chroma down"):
                delete_existing_source_versions("default", "src_doc_v1")
        assert catalog.get_document("doc_v1") is not None
        assert catalog.count_chunks("doc_v1") == 2


class TestDeleteRetryAndRecovery:
    """自动重试 + 删除日志 + 手动 retry/rollback 恢复链。"""

    def test_catalog_retry_succeeds_on_third_attempt(self, catalog, monkeypatch):
        _seed(catalog, "doc1", n_chunks=2)
        monkeypatch.setattr("agentnexus.rag.kb_service.time.sleep", lambda s: None)
        attempts = {"n": 0}
        real_delete = catalog.delete_document

        def flaky(document_id):
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise RuntimeError("db locked")
            real_delete(document_id)

        monkeypatch.setattr(catalog, "delete_document", flaky)
        with patch("agentnexus.rag.kb_service.delete_documents"):
            from agentnexus.rag.kb_service import delete_document_and_vectors
            deleted = delete_document_and_vectors("default", "doc1")
        assert deleted == 2
        assert attempts["n"] == 3, "前两次失败后第三次应成功"
        assert catalog.get_document("doc1") is None
        assert catalog.list_deletion_logs(status="failed") == [], "成功路径不写失败日志"

    def test_three_failures_raise_with_journal_and_options(self, catalog, monkeypatch):
        _seed(catalog, "doc1", n_chunks=2)
        monkeypatch.setattr("agentnexus.rag.kb_service.time.sleep", lambda s: None)
        monkeypatch.setattr(
            catalog, "delete_document",
            MagicMock(side_effect=RuntimeError("db locked")))
        with patch("agentnexus.rag.kb_service.delete_documents"):
            from agentnexus.rag.kb_service import (
                DeleteIncompleteError,
                delete_document_and_vectors,
            )
            with pytest.raises(DeleteIncompleteError) as exc_info:
                delete_document_and_vectors("default", "doc1")
        assert catalog.delete_document.call_count == 3, "默认重试 3 次"
        err = exc_info.value
        assert err.deleted_vectors == 2
        logs = catalog.list_deletion_logs(status="failed")
        assert len(logs) == 1 and logs[0]["document_id"] == "doc1"
        assert logs[0]["chunk_ids"] == ["doc1_ch0", "doc1_ch1"]
        assert err.log_id == logs[0]["id"]

    def test_retry_completes_deletion(self, catalog, monkeypatch):
        _seed(catalog, "doc1", n_chunks=2)
        monkeypatch.setattr("agentnexus.rag.kb_service.time.sleep", lambda s: None)
        monkeypatch.setattr(
            catalog, "delete_document",
            MagicMock(side_effect=RuntimeError("db locked")))
        with patch("agentnexus.rag.kb_service.delete_documents"):
            from agentnexus.rag.kb_service import (
                DeleteIncompleteError,
                delete_document_and_vectors,
            )
            with pytest.raises(DeleteIncompleteError) as exc_info:
                delete_document_and_vectors("default", "doc1")
        log_id = exc_info.value.log_id
        # 故障恢复后手动 retry（catalog 删除恢复正常）
        from agentnexus.rag.store import KnowledgeBaseCatalog
        monkeypatch.setattr(catalog, "delete_document",
                            lambda doc_id: KnowledgeBaseCatalog.delete_document(catalog, doc_id))
        with patch("agentnexus.rag.kb_service.delete_documents"):
            from agentnexus.rag.kb_service import retry_failed_deletion
            result = retry_failed_deletion(log_id)
        assert result["status"] == "completed" and result["changed"] is True
        assert catalog.get_document("doc1") is None, "retry 必须补完 catalog 删除"
        assert catalog.get_deletion_log(log_id)["status"] == "completed"

    def test_rollback_restores_vectors(self, catalog, monkeypatch):
        _seed(catalog, "doc1", n_chunks=2)
        monkeypatch.setattr("agentnexus.rag.kb_service.time.sleep", lambda s: None)
        monkeypatch.setattr(
            catalog, "delete_document",
            MagicMock(side_effect=RuntimeError("db locked")))
        with patch("agentnexus.rag.kb_service.delete_documents"):
            from agentnexus.rag.kb_service import (
                DeleteIncompleteError,
                delete_document_and_vectors,
            )
            with pytest.raises(DeleteIncompleteError) as exc_info:
                delete_document_and_vectors("default", "doc1")
        log_id = exc_info.value.log_id
        with patch("agentnexus.rag.kb_service.upsert_documents") as mock_upsert:
            from agentnexus.rag.kb_service import rollback_failed_deletion
            result = rollback_failed_deletion(log_id)
        assert result["status"] == "rolled_back" and result["restored_vectors"] == 2
        assert mock_upsert.call_args.kwargs["ids"] == ["doc1_ch0", "doc1_ch1"]
        assert catalog.get_document("doc1") is not None, "撤回后文档仍在"
        assert catalog.get_deletion_log(log_id)["status"] == "rolled_back"

    def test_rollback_impossible_when_catalog_text_gone(self, catalog):
        log_id = catalog.log_deletion_failure("default", "ghost_doc", ["ghost_ch"], "x")
        from agentnexus.rag.kb_service import rollback_failed_deletion
        with pytest.raises(RuntimeError, match="无法撤回"):
            rollback_failed_deletion(log_id)

    def test_recovery_rejects_unknown_log(self, catalog):
        from agentnexus.rag.kb_service import retry_failed_deletion
        with pytest.raises(KeyError):
            retry_failed_deletion(9999)


class TestReconcileKb:
    """对账：孤儿向量补删 + 缺失向量补嵌，双向修复。"""

    def _fake_collection(self, ids: list[str]):
        col = MagicMock()
        col.get.return_value = {"ids": ids}
        return col

    def test_orphans_deleted_missing_reembedded(self, catalog):
        _seed(catalog, "doc1", n_chunks=2)  # catalog: doc1_ch0, doc1_ch1
        # Chroma 侧：doc1_ch0（正常）+ ghost_ch（孤儿）；doc1_ch1 缺失
        fake_col = self._fake_collection(["doc1_ch0", "ghost_ch"])
        with patch("agentnexus.rag.kb_service.get_collection", return_value=fake_col), \
             patch("agentnexus.rag.kb_service.delete_documents") as mock_del, \
             patch("agentnexus.rag.kb_service.upsert_documents") as mock_upsert:
            from agentnexus.rag.kb_service import reconcile_kb
            stats = reconcile_kb("default")
        assert stats == {"orphans_deleted": 1, "vectors_reembedded": 1}
        mock_del.assert_called_once_with(ids=["ghost_ch"], namespace="default")
        upsert_kwargs = mock_upsert.call_args.kwargs
        assert upsert_kwargs["ids"] == ["doc1_ch1"]
        assert upsert_kwargs["namespace"] == "default"

    def test_dry_run_reports_without_repair(self, catalog):
        _seed(catalog, "doc1", n_chunks=1)
        fake_col = self._fake_collection(["ghost_ch"])
        with patch("agentnexus.rag.kb_service.get_collection", return_value=fake_col), \
             patch("agentnexus.rag.kb_service.delete_documents") as mock_del, \
             patch("agentnexus.rag.kb_service.upsert_documents") as mock_upsert:
            from agentnexus.rag.kb_service import reconcile_kb
            stats = reconcile_kb("default", repair=False)
        assert stats == {"orphans_deleted": 1, "vectors_reembedded": 1}
        mock_del.assert_not_called()
        mock_upsert.assert_not_called()

    def test_consistent_state_is_noop(self, catalog):
        _seed(catalog, "doc1", n_chunks=2)
        fake_col = self._fake_collection(["doc1_ch0", "doc1_ch1"])
        with patch("agentnexus.rag.kb_service.get_collection", return_value=fake_col), \
             patch("agentnexus.rag.kb_service.delete_documents") as mock_del, \
             patch("agentnexus.rag.kb_service.upsert_documents") as mock_upsert:
            from agentnexus.rag.kb_service import reconcile_kb
            stats = reconcile_kb("default")
        assert stats == {"orphans_deleted": 0, "vectors_reembedded": 0}
        mock_del.assert_not_called()
        mock_upsert.assert_not_called()


class TestMidStateSearchLeak:
    """泄露实证：中间态（向量已删、catalog 还在）下，"已删除"文档仍可被检索到。

    检索是双路的（dense=Chroma + sparse=catalog BM25）。dense 路已查不到，
    但 sparse 路从 catalog 重建，照常命中——删除动作对外表现为没删干净。
    """

    def test_midstate_document_still_searchable_via_sparse(self, catalog, monkeypatch):
        # 语料要有多个文档，BM25 的 idf 才不退化（单文档语料所有词 idf≤0）
        for i in range(4):
            _seed(catalog, f"doc_noise{i}", n_chunks=1)
        _seed(catalog, "doc_secret", n_chunks=1)
        # 给 secret chunk 换上独特内容
        from agentnexus.rag.models import ChunkRecord
        from agentnexus.storage.chroma import resolve_collection_name
        kb_id = resolve_collection_name(namespace="default")
        catalog.upsert_chunks([ChunkRecord(
            chunk_id="doc_secret_ch0", kb_id=kb_id, document_id="doc_secret",
            document_version=1, chunk_index=0,
            text="内部机密配方 X-42 的完整工艺流程")])
        # 模拟中间态：catalog 完整，Chroma 向量已删（dense 候选为空）
        monkeypatch.setattr(
            "agentnexus.rag.retriever.get_knowledge_base_catalog", lambda: catalog)
        from agentnexus.rag.retriever import HybridRetriever
        retriever = HybridRetriever(namespace="default")
        retriever.rebuild_from_catalog()
        # dense 为空 = Chroma 侧已删；仅 sparse 路工作
        results = retriever.search("机密配方", dense_results=[], min_score=0.0)
        leaked = [r for r in results if r.id.startswith("doc_secret")]
        assert leaked, "中间态下 sparse 路仍能搜到『已删』文档——这就是泄露"
        assert "机密配方" in leaked[0].text

    def test_fully_deleted_document_not_searchable(self, catalog, monkeypatch):
        """对照组：双清后任何路都搜不到。"""
        for i in range(4):
            _seed(catalog, f"doc_noise{i}", n_chunks=1)
        _seed(catalog, "doc_secret", n_chunks=1)
        from agentnexus.rag.models import ChunkRecord
        from agentnexus.storage.chroma import resolve_collection_name
        kb_id = resolve_collection_name(namespace="default")
        catalog.upsert_chunks([ChunkRecord(
            chunk_id="doc_secret_ch0", kb_id=kb_id, document_id="doc_secret",
            document_version=1, chunk_index=0,
            text="内部机密配方 X-42 的完整工艺流程")])
        with patch("agentnexus.rag.kb_service.delete_documents"):
            from agentnexus.rag.kb_service import delete_document_and_vectors
            delete_document_and_vectors("default", "doc_secret")
        monkeypatch.setattr(
            "agentnexus.rag.retriever.get_knowledge_base_catalog", lambda: catalog)
        from agentnexus.rag.retriever import HybridRetriever
        retriever = HybridRetriever(namespace="default")
        retriever.rebuild_from_catalog()
        results = retriever.search("机密配方", dense_results=[], min_score=0.0)
        assert all(not r.id.startswith("doc_secret") for r in results)


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
