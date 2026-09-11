"""Knowledge-base ingestion and search service helpers used by CLI surfaces."""

from __future__ import annotations

import time
import uuid

from agentnexus.rag.ingestion import ingest_document
from agentnexus.rag.models import IngestedDocument, IngestionRunRecord, KnowledgeBaseRecord
from agentnexus.rag.retriever import HybridRetriever, expand_queries
from agentnexus.rag.store import get_knowledge_base_catalog
from agentnexus.storage.chroma import (
    chunk_metadata_to_chroma,
    delete_documents,
    get_collection,
    resolve_collection_name,
    upsert_documents,
)
from agentnexus.storage.chroma import search as chroma_search
from agentnexus.tools.kb_search import _build_search_where


def default_kb_record(namespace: str) -> KnowledgeBaseRecord:
    collection_name = resolve_collection_name(namespace=namespace)
    return KnowledgeBaseRecord(
        kb_id=collection_name,
        namespace=namespace,
        display_name=namespace,
        collection_name=collection_name,
    )


def reconcile_kb(namespace: str, *, repair: bool = True) -> dict[str, int]:
    """对账 catalog 与 Chroma 的 chunk 集合，双向修复。

    - 孤儿向量（Chroma 有、catalog 无：删除时 Chroma 失败或历史泄漏）→ 从 Chroma 补删
    - 缺失向量（catalog 有、Chroma 无：删除中间态/嵌入失败）→ 用 catalog 的
      indexed_text 重新嵌入写入，恢复 dense 可检索
    实测全量成本 ~220ms @ 20K chunks（~1.2s @ 100K），适合启动时/定时调用，
    不适合每次删除后调用。
    """
    catalog = get_knowledge_base_catalog()
    kb = catalog.get_knowledge_base(namespace)
    if kb is None:
        return {"orphans_deleted": 0, "vectors_reembedded": 0}
    chunks = catalog.list_chunks_by_kb(kb.kb_id)
    catalog_ids = {chunk.chunk_id for chunk in chunks}
    chroma_ids = set(get_collection(namespace=namespace).get(include=[])["ids"])

    stats = {"orphans_deleted": 0, "vectors_reembedded": 0}
    if not repair:
        stats["orphans_deleted"] = len(chroma_ids - catalog_ids)
        stats["vectors_reembedded"] = len(catalog_ids - chroma_ids)
        return stats

    orphans = sorted(chroma_ids - catalog_ids)
    if orphans:
        delete_documents(ids=orphans, namespace=namespace)
        stats["orphans_deleted"] = len(orphans)

    missing = [chunk for chunk in chunks if chunk.chunk_id not in chroma_ids]
    if missing:
        upsert_documents(
            [chunk.indexed_text or chunk.text for chunk in missing],
            metadatas=[chunk_metadata_to_chroma(chunk) for chunk in missing],
            ids=[chunk.chunk_id for chunk in missing],
            namespace=namespace,
        )
        stats["vectors_reembedded"] = len(missing)
    return stats


class DeleteIncompleteError(Exception):
    """Chroma 已删、catalog 重试 3 次仍失败：文档处于泄露中间态。

    携带 log_id——用户可凭它 retry（补完删除）或 rollback（重嵌向量撤回）。
    """

    def __init__(self, log_id: int, document_id: str, deleted_vectors: int,
                 cause: Exception):
        super().__init__(
            f"删除未完成: {document_id}（向量已删 {deleted_vectors} 个，目录删除失败）: {cause}")
        self.log_id = log_id
        self.document_id = document_id
        self.deleted_vectors = deleted_vectors
        self.cause = cause


def _delete_catalog_with_retry(catalog, document_id: str,
                               attempts: int = 3, base_delay: float = 0.1) -> None:
    """catalog 删除失败默认重试（指数退避），3 次无果抛最后一次异常。"""
    last: Exception | None = None
    for i in range(attempts):
        try:
            catalog.delete_document(document_id)
            return
        except Exception as e:
            last = e
            if i < attempts - 1:
                time.sleep(base_delay * (2 ** i))
    raise last  # type: ignore[misc]


def delete_document_and_vectors(namespace: str, document_id: str) -> int:
    """完整删除一个文档：Chroma 向量 + catalog 文档（chunks 由 FK 级联清理）。

    顺序保证失败安全：先 Chroma 后 catalog——Chroma 失败则两侧都在（可安全
    重试）；catalog 失败则自动重试 3 次，仍失败则写入删除日志并抛
    DeleteIncompleteError，用户可 retry 补完或 rollback 撤回（重嵌向量）。
    文档不存在时安全返回 0。
    """
    catalog = get_knowledge_base_catalog()
    document = catalog.get_document(document_id)
    if document is None:
        return 0
    chunks = catalog.list_chunks(document_id)
    chunk_ids = [chunk.chunk_id for chunk in chunks]
    deleted = 0
    if chunk_ids:
        delete_documents(ids=chunk_ids, namespace=namespace)
        deleted = len(chunk_ids)
    try:
        _delete_catalog_with_retry(catalog, document_id)
    except Exception as e:
        try:
            log_id = catalog.log_deletion_failure(namespace, document_id, chunk_ids, str(e))
        except Exception:
            log_id = -1  # 日志都写不进时，异常信息里仍带全上下文
        raise DeleteIncompleteError(log_id, document_id, deleted, e) from e
    return deleted


def retry_failed_deletion(log_id: int) -> dict:
    """补完一次失败的删除：重试 catalog 删除（Chroma 侧幂等重删兜底）。"""
    catalog = get_knowledge_base_catalog()
    entry = catalog.get_deletion_log(log_id)
    if entry is None:
        raise KeyError(f"deletion log not found: {log_id}")
    if entry["status"] != "failed":
        return {"log_id": log_id, "status": entry["status"], "changed": False}
    try:
        _delete_catalog_with_retry(catalog, entry["document_id"])
    except Exception as e:
        catalog.update_deletion_log_status(log_id, "failed", str(e))
        raise
    # 幂等兜底：若中间态期间有人重嵌过，确保向量也被清掉
    if entry["chunk_ids"]:
        delete_documents(ids=entry["chunk_ids"], namespace=entry["namespace"])
    catalog.update_deletion_log_status(log_id, "completed")
    return {"log_id": log_id, "status": "completed", "changed": True}


def rollback_failed_deletion(log_id: int) -> dict:
    """撤回一次失败的删除：用 catalog 留存的 indexed_text 重嵌向量。"""
    catalog = get_knowledge_base_catalog()
    entry = catalog.get_deletion_log(log_id)
    if entry is None:
        raise KeyError(f"deletion log not found: {log_id}")
    if entry["status"] != "failed":
        return {"log_id": log_id, "status": entry["status"], "changed": False}
    chunks = catalog.list_chunks(entry["document_id"])
    if not chunks:
        raise RuntimeError(
            f"无法撤回: 目录中已无 {entry['document_id']} 的原文（可能已被后续操作删除）")
    upsert_documents(
        [chunk.indexed_text or chunk.text for chunk in chunks],
        metadatas=[chunk_metadata_to_chroma(chunk) for chunk in chunks],
        ids=[chunk.chunk_id for chunk in chunks],
        namespace=entry["namespace"],
    )
    catalog.update_deletion_log_status(log_id, "rolled_back")
    return {"log_id": log_id, "status": "rolled_back",
            "restored_vectors": len(chunks), "changed": True}


def delete_existing_source_versions(namespace: str, source_id: str) -> int:
    catalog = get_knowledge_base_catalog()
    kb_record = default_kb_record(namespace)
    catalog.upsert_knowledge_base(kb_record)

    existing_documents = catalog.list_documents_by_source(kb_record.kb_id, source_id)
    if not existing_documents:
        return 0

    deleted_chunks = 0
    for document in existing_documents:
        chunks = catalog.list_chunks(document.document_id)
        chunk_ids = [chunk.chunk_id for chunk in chunks]
        if chunk_ids:
            delete_documents(ids=chunk_ids, namespace=namespace)
            deleted_chunks += len(chunk_ids)
        catalog.delete_document(document.document_id)
    return deleted_chunks


def persist_ingested_document(artifacts: IngestedDocument, namespace: str) -> dict[str, int]:
    kb_record = default_kb_record(namespace)
    catalog = get_knowledge_base_catalog()
    catalog.upsert_knowledge_base(kb_record)

    artifacts.document.kb_id = kb_record.kb_id
    replaced_chunks = delete_existing_source_versions(namespace, artifacts.document.source_id)
    catalog.upsert_document(artifacts.document)

    for chunk in artifacts.chunks:
        chunk.kb_id = kb_record.kb_id
    catalog.upsert_chunks(artifacts.chunks)

    upsert_documents(
        [chunk.indexed_text or chunk.text for chunk in artifacts.chunks],
        metadatas=[chunk_metadata_to_chroma(chunk) for chunk in artifacts.chunks],
        ids=[chunk.chunk_id for chunk in artifacts.chunks],
        namespace=namespace,
    )
    return {"replaced_chunks": replaced_chunks, "written_chunks": len(artifacts.chunks)}


def start_ingestion_run(namespace: str, source_uri: str, run_id: str | None = None) -> IngestionRunRecord:
    kb_record = default_kb_record(namespace)
    catalog = get_knowledge_base_catalog()
    catalog.upsert_knowledge_base(kb_record)
    run = IngestionRunRecord(
        run_id=run_id or f"ingest_{uuid.uuid4().hex[:12]}",
        kb_id=kb_record.kb_id,
        status="running",
        source_uri=source_uri,
    )
    catalog.upsert_ingestion_run(run)
    return run


def finish_ingestion_run(
    run: IngestionRunRecord,
    *,
    status: str,
    documents_seen: int,
    chunks_written: int,
    error_message: str = "",
    metadata: dict | None = None,
) -> None:
    run.status = status
    run.documents_seen = documents_seen
    run.chunks_written = chunks_written
    run.error_message = error_message
    # Merge metadata instead of replacing to preserve progress keys
    run.metadata = {**run.metadata, **(metadata or {})}
    run.finished_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    catalog = get_knowledge_base_catalog()
    catalog.upsert_ingestion_run(run)


def _update_run_progress(
    run: IngestionRunRecord,
    *,
    stage: str,
    stage_pct: int,
    message: str = "",
) -> None:
    """Update the ingestion run record with current progress."""
    run.metadata = {
        **run.metadata,
        "progress_stage": stage,
        "progress_pct": stage_pct,
        "progress_message": message,
    }
    catalog = get_knowledge_base_catalog()
    catalog.upsert_ingestion_run(run)


def ingest_one_document(
    filepath: str,
    *,
    namespace: str,
    enable_contextual: bool,
    llm_client=None,
    run_id: str | None = None,
    source_uri: str | None = None,
) -> tuple[IngestedDocument, IngestionRunRecord]:
    from agentnexus.core.hooks import HookType, get_hook_manager

    effective_uri = source_uri or filepath
    hook_mgr = get_hook_manager()
    hook_mgr.fire(HookType.BEFORE_KB_INGEST, {
        "filepath": filepath, "namespace": namespace,
        "enable_contextual": enable_contextual,
    })

    run = start_ingestion_run(namespace, effective_uri, run_id=run_id)
    started_at = time.perf_counter()

    # Stage: loading
    _update_run_progress(run, stage="loading", stage_pct=10, message="Loading document")

    try:
        def _enrichment_progress(current: int, total: int) -> None:
            pct = 30 + int(40 * current / total) if total > 0 else 50
            _update_run_progress(
                run,
                stage="enriching",
                stage_pct=pct,
                message=f"Enriching chunks ({current}/{total})",
            )

        artifacts = ingest_document(
            filepath,
            chunk_size=512,
            enable_contextual=enable_contextual,
            llm_client=llm_client,
            enrichment_progress_callback=_enrichment_progress if enable_contextual else None,
        )

        # Stage: embedding + persisting
        _update_run_progress(run, stage="embedding", stage_pct=80, message="Generating embeddings")
        stats = persist_ingested_document(artifacts, namespace)
        _update_run_progress(run, stage="persisting", stage_pct=90, message="Saving to database")
    except Exception as exc:
        finish_ingestion_run(
            run,
            status="failed",
            documents_seen=0,
            chunks_written=0,
            error_message=str(exc),
        )
        raise

    finish_ingestion_run(
        run,
        status="completed",
        documents_seen=1,
        chunks_written=stats["written_chunks"],
        metadata={
            "replaced_chunks": stats["replaced_chunks"],
            "duration_ms": round((time.perf_counter() - started_at) * 1000, 2),
            "progress_stage": "completed",
            "progress_pct": 100,
            "progress_message": "Done",
        },
    )

    hook_mgr.fire(HookType.AFTER_KB_INGEST, {
        "filepath": filepath, "namespace": namespace,
        "written_chunks": stats["written_chunks"],
        "replaced_chunks": stats["replaced_chunks"],
    })

    # Auto-generate wiki pages from the ingested document
    _update_run_progress(run, stage="wiki", stage_pct=95, message="Generating wiki pages")
    try:
        from agentnexus.wiki.wiki_service import WikiService

        wiki = WikiService()
        source_text = artifacts.document.raw_text or artifacts.document.indexed_text or artifacts.document.content
        wiki.ingest_source(
            source_text=source_text,
            source_uri=effective_uri,
            source_namespace=namespace,
        )
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("Wiki auto-generation failed (non-fatal): %s", exc)

    return artifacts, run


def search_kb(
    query: str,
    *,
    namespace: str,
    top_k: int,
    view: str,
    source: str = "",
    file_format: str = "",
    section_title: str = "",
    page_number: int | None = None,
    block_type: str = "",
    has_code: bool | None = None,
    has_list: bool | None = None,
    heading_depth: int | None = None,
):
    retriever = HybridRetriever(namespace=namespace)
    retriever.rebuild_from_catalog()
    if not retriever._chunks:
        return []

    if retriever._reranker is None:
        retriever.load_reranker()

    where = _build_search_where(
        source=source,
        file_format=file_format,
        section_title=section_title,
        page_number=page_number,
        block_type=block_type,
        has_code=has_code,
        has_list=has_list,
        heading_depth=heading_depth,
    )
    dense_fused: dict[str, float] = {}
    for search_query in expand_queries(query):
        dense_results = chroma_search(
            search_query,
            limit=max(top_k * 2, 10),
            namespace=namespace,
            where=where,
        )
        for rank, item in enumerate(dense_results):
            dense_fused[item["id"]] = dense_fused.get(item["id"], 0.0) + 1.0 / (60 + rank + 1)
    dense = sorted(dense_fused.items(), key=lambda x: x[1], reverse=True)
    results = retriever.search(
        query,
        dense,
        top_k=top_k,
        min_score=0.0,
        metadata_filters=where,
    )
    if not results:
        return []
    return retriever.expand_contexts(results, view=view)
