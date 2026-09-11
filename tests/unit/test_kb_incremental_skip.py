"""增量第一层：内容未变更的重复摄入必须整体跳过。

document_version 是内容寻址哈希，同 source 同版本 → 索引必然一致。
业界标准做法（LlamaIndex docstore / LangChain RecordManager / TypeGraph
增量重索引），对定时同步类场景（每天扫一遍目录）收益 95%+。
"""
import pytest

from agentnexus.rag.ids import (
    make_chunk_id,
    make_document_version,
    make_source_id,
)
from agentnexus.rag.models import ChunkRecord, IngestedDocument, SourceDocument


@pytest.fixture()
def catalog(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTNEXUS_HOME", str(tmp_path))
    import agentnexus.core.config as cfg
    cfg._settings_cache = None
    import agentnexus.rag.kb_service as kbs
    from agentnexus.rag.store import KnowledgeBaseCatalog
    from agentnexus.storage.chroma import reset_storage_client, resolve_collection_name
    cat = KnowledgeBaseCatalog(db_path=str(tmp_path / "kb.db"))
    monkeypatch.setattr(kbs, "get_knowledge_base_catalog", lambda: cat)
    kb_id = resolve_collection_name(namespace="default")
    from agentnexus.rag.models import KnowledgeBaseRecord
    cat.upsert_knowledge_base(KnowledgeBaseRecord(
        kb_id=kb_id, namespace="default", display_name="d", collection_name=kb_id))
    yield cat
    cat.close()


def _artifacts(source_uri: str, content: str, n_chunks: int = 3) -> IngestedDocument:
    source_id = make_source_id(source_uri)
    version = make_document_version(source_id, content)
    document = SourceDocument(
        document_id=version, kb_id="", source_id=source_id,
        source_uri=source_uri, document_version=version, content=content)
    chunks = [
        ChunkRecord(
            chunk_id=make_chunk_id(version, i, f"{content}#{i}"),
            kb_id="", document_id=version, document_version=version,
            chunk_index=i, text=f"{content}#{i}")
        for i in range(n_chunks)
    ]
    return IngestedDocument(document=document, chunks=chunks)


class TestIncrementalSkip:
    def test_unchanged_content_skips_entirely(self, catalog):
        from agentnexus.rag.kb_service import persist_ingested_document
        artifacts = _artifacts("doc.md", "第一版内容")
        first = persist_ingested_document(artifacts, "default")
        assert first["written_chunks"] == 3 and not first.get("skipped")

        with pytest.MonkeyPatch().context() as mp:
            import agentnexus.rag.kb_service as kbs
            calls = {"del": 0, "ups": 0}
            mp.setattr(kbs, "delete_documents", lambda **kw: calls.__setitem__("del", calls["del"] + 1))
            mp.setattr(kbs, "upsert_documents", lambda *a, **kw: calls.__setitem__("ups", calls["ups"] + 1))
            second = persist_ingested_document(
                _artifacts("doc.md", "第一版内容"), "default")
        assert second == {"replaced_chunks": 0, "written_chunks": 0, "skipped": True}
        assert calls == {"del": 0, "ups": 0}, "跳过路径不得触碰 Chroma"
        assert catalog.count_chunks(catalog.list_documents()[0].document_id) == 3

    def test_changed_content_full_replace(self, catalog):
        from agentnexus.rag.kb_service import persist_ingested_document
        persist_ingested_document(_artifacts("doc.md", "第一版"), "default")
        result = persist_ingested_document(_artifacts("doc.md", "第二版"), "default")
        assert not result.get("skipped")
        assert result["replaced_chunks"] == 3 and result["written_chunks"] == 3
        docs = catalog.list_documents()
        assert len(docs) == 1 and docs[0].content == "第二版"

    def test_multi_version_anomaly_does_not_skip(self, catalog):
        """同来源存在多个文档 = 历史异常态，必须走替换路径自愈而非跳过。"""
        from agentnexus.rag.kb_service import persist_ingested_document
        persist_ingested_document(_artifacts("doc.md", "第一版"), "default")
        # 手工制造异常：同 source 塞第二个文档（旧时代遗留/部分失败痕迹）
        extra = _artifacts("doc.md", "幽灵版").document
        extra.kb_id = catalog.list_documents()[0].kb_id
        from agentnexus.rag.ids import make_document_version, make_source_id
        extra.document_id = "ghost_" + extra.document_id
        extra.document_version = make_document_version(
            make_source_id("doc.md"), "幽灵版内容")
        catalog.upsert_document(extra)
        result = persist_ingested_document(_artifacts("doc.md", "第一版"), "default")
        assert not result.get("skipped"), "多版本异常态不得跳过"
        assert len(catalog.list_documents()) == 1, "替换路径必须收敛到单版本"

    def test_matching_version_without_chunks_does_not_skip(self, catalog):
        """版本一致但 chunks 为空 = 不完整写入，重写补齐。"""
        from agentnexus.rag.kb_service import persist_ingested_document
        artifacts = _artifacts("doc.md", "内容")
        persist_ingested_document(artifacts, "default")
        # 删掉 chunks 模拟不完整态
        catalog._conn.execute(
            "DELETE FROM document_chunks WHERE document_id = ?",
            (artifacts.document.document_id,))
        catalog._conn.commit()
        result = persist_ingested_document(_artifacts("doc.md", "内容"), "default")
        assert not result.get("skipped")
        assert result["written_chunks"] == 3
