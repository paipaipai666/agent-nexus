"""Perf: 知识库更新路径（删旧版本 + 重建写入）。

更新 = 全量删除同来源旧版本 + 全量重新写入。实测结构成本随 chunk 数线性
（~0.5ms/chunk：删除 0.19ms + 写入 0.36ms）。本测试用 stub 向量隔离嵌入
成本——嵌入成本由 test_ingestion_throughput 覆盖；contextual LLM 增强
（若开启）是每 chunk 一次 LLM 调用，是真实重摄入的最大头，不在本文件范围。

阈值基于本机实测 2 倍余量（删除 192ms/1000, 写入 348ms/1000）：
"""
import time

import pytest

UPDATE_N_CHUNKS = 1000
UPDATE_DELETE_MAX_S = 0.6
UPDATE_REBUILD_MAX_S = 0.9
UPDATE_TOTAL_MAX_S = 1.4


@pytest.fixture()
def update_env(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTNEXUS_HOME", str(tmp_path))
    import agentnexus.core.config as cfg
    cfg._settings_cache = None
    import agentnexus.rag.kb_service as kbs
    from agentnexus.rag.models import KnowledgeBaseRecord
    from agentnexus.rag.store import KnowledgeBaseCatalog
    from agentnexus.storage.chroma import (get_collection,
                                           reset_storage_client,
                                           resolve_collection_name)
    reset_storage_client()  # 清掉模块级 _collections 缓存，避免跨测试串目录
    cat = KnowledgeBaseCatalog(db_path=str(tmp_path / "kb.db"))
    kbs.get_knowledge_base_catalog = lambda: cat
    kb_id = resolve_collection_name(namespace="default")
    cat.upsert_knowledge_base(KnowledgeBaseRecord(
        kb_id=kb_id, namespace="default", display_name="d", collection_name=kb_id))
    col = get_collection(namespace="default")
    yield {"catalog": cat, "col": col, "kb_id": kb_id, "tmp": tmp_path, "kbs": kbs}
    cat.close()


def _seed(env, n_chunks: int, doc_id: str):
    from agentnexus.rag.models import ChunkRecord, SourceDocument
    cat, col, kb_id = env["catalog"], env["col"], env["kb_id"]
    chunks = [ChunkRecord(chunk_id=f"c{n_chunks}_{i}_{doc_id}", kb_id=kb_id,
                          document_id=doc_id, document_version=1, chunk_index=0,
                          text=f"内容段 {i} " + "文" * 400)
              for i in range(n_chunks)]
    cat.upsert_document(SourceDocument(document_id=doc_id, kb_id=kb_id, source_id="s1",
                                       source_uri="doc.md", document_version=1, content="x"))
    cat.upsert_chunks(chunks)
    for start in range(0, n_chunks, 500):
        batch = chunks[start:start + 500]
        col.add(ids=[c.chunk_id for c in batch], embeddings=[[0.01] * 8] * len(batch),
                documents=[c.text for c in batch])
    return chunks


class TestUpdatePathPerf:
    """更新 = 删旧 + 重建。双阶段分别计时，超阈值即回归信号。"""

    def test_update_delete_and_rebuild(self, update_env):
        kbs = update_env["kbs"]
        chunks = _seed(update_env, UPDATE_N_CHUNKS, "doc_v1")
        from agentnexus.rag.kb_service import delete_existing_source_versions

        t0 = time.perf_counter()
        removed = delete_existing_source_versions("default", "s1")
        t_delete = time.perf_counter() - t0
        assert removed == UPDATE_N_CHUNKS

        t1 = time.perf_counter()
        _seed(update_env, UPDATE_N_CHUNKS, "doc_v2")
        t_rebuild = time.perf_counter() - t1

        total = t_delete + t_rebuild
        print(f"\n删除 {t_delete*1000:.0f}ms, 重建 {t_rebuild*1000:.0f}ms, "
              f"合计 {total:.2f}s @ {UPDATE_N_CHUNKS} chunks")
        assert t_delete < UPDATE_DELETE_MAX_S, (
            f"删除旧版本 {t_delete:.2f}s > {UPDATE_DELETE_MAX_S}s——HNSW 删除可能退化")
        assert t_rebuild < UPDATE_REBUILD_MAX_S, (
            f"重建写入 {t_rebuild:.2f}s > {UPDATE_REBUILD_MAX_S}s——批量插入可能退化")
        assert total < UPDATE_TOTAL_MAX_S

    def test_update_replace_semantics(self, update_env):
        """替换语义不变：更新后旧 chunk 全清、新 chunk 在位、catalog 与 Chroma 一致。"""
        kbs = update_env["kbs"]
        _seed(update_env, 50, "doc_old")
        from agentnexus.rag.kb_service import delete_existing_source_versions
        delete_existing_source_versions("default", "s1")
        _seed(update_env, 50, "doc_new")
        cat, col = update_env["catalog"], update_env["col"]
        catalog_ids = {c.chunk_id for c in cat.list_chunks_by_kb(update_env["kb_id"])}
        chroma_ids = set(col.get(include=[])["ids"])
        assert catalog_ids == chroma_ids, "更新后 catalog 与 Chroma 必须收敛一致"
        assert all(c.startswith("c50_") and c.endswith("doc_new") for c in catalog_ids)
