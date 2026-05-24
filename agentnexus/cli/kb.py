"""CLI kb add/list/search commands"""
import os
import time
import uuid

import typer

from agentnexus.core.config import get_settings
from agentnexus.rag.chroma_client import (
    chunk_metadata_to_chroma,
    delete_documents,
    get_collection,
    resolve_collection_name,
    search as chroma_search,
    upsert_documents,
)
from agentnexus.rag.ingestion import ingest_document
from agentnexus.rag.models import IngestedDocument, IngestionRunRecord, KnowledgeBaseRecord
from agentnexus.rag.retriever import HybridRetriever, expand_queries
from agentnexus.rag.store import get_knowledge_base_catalog

from . import console, kb_app


def _default_kb_record(namespace: str) -> KnowledgeBaseRecord:
    collection_name = resolve_collection_name(namespace=namespace)
    return KnowledgeBaseRecord(
        kb_id=collection_name,
        namespace=namespace,
        display_name=namespace,
        collection_name=collection_name,
    )


def _delete_existing_source_versions(namespace: str, source_id: str) -> int:
    catalog = get_knowledge_base_catalog()
    kb_record = _default_kb_record(namespace)
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


def _persist_ingested_document(artifacts: IngestedDocument, namespace: str) -> dict[str, int]:
    kb_record = _default_kb_record(namespace)
    catalog = get_knowledge_base_catalog()
    catalog.upsert_knowledge_base(kb_record)

    artifacts.document.kb_id = kb_record.kb_id
    replaced_chunks = _delete_existing_source_versions(namespace, artifacts.document.source_id)
    catalog.upsert_document(artifacts.document)

    for chunk in artifacts.chunks:
        chunk.kb_id = kb_record.kb_id
    catalog.upsert_chunks(artifacts.chunks)

    upsert_documents(
        [chunk.text for chunk in artifacts.chunks],
        metadatas=[chunk_metadata_to_chroma(chunk) for chunk in artifacts.chunks],
        ids=[chunk.chunk_id for chunk in artifacts.chunks],
        namespace=namespace,
    )
    return {"replaced_chunks": replaced_chunks, "written_chunks": len(artifacts.chunks)}


def _start_ingestion_run(namespace: str, source_uri: str) -> IngestionRunRecord:
    kb_record = _default_kb_record(namespace)
    catalog = get_knowledge_base_catalog()
    catalog.upsert_knowledge_base(kb_record)
    run = IngestionRunRecord(
        run_id=f"ingest_{uuid.uuid4().hex[:12]}",
        kb_id=kb_record.kb_id,
        status="running",
        source_uri=source_uri,
    )
    catalog.upsert_ingestion_run(run)
    return run


def _finish_ingestion_run(
    run: IngestionRunRecord,
    *,
    status: str,
    documents_seen: int,
    chunks_written: int,
    error_message: str = "",
    metadata: dict | None = None,
):
    run.status = status
    run.documents_seen = documents_seen
    run.chunks_written = chunks_written
    run.error_message = error_message
    run.metadata = dict(metadata or {})
    run.finished_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    catalog = get_knowledge_base_catalog()
    catalog.upsert_ingestion_run(run)


def _build_search_where(
    source: str | None = None,
    file_format: str | None = None,
    section_title: str | None = None,
    page_number: int | None = None,
) -> dict | None:
    where: dict[str, object] = {}
    if source:
        where["source_uri"] = source
    if file_format:
        where["format"] = file_format
    if section_title:
        where["section_title"] = section_title
    if page_number is not None:
        where["page_number"] = page_number
    return where or None


@kb_app.command("add")
def kb_add(path: str = typer.Argument(..., help="文档路径或目录")):
    """添加文档到知识库"""
    settings = get_settings()
    namespace = settings.rag_default_namespace
    llm_client = None
    if settings.enable_contextual_retrieval:
        from agentnexus.core.llm import AgentLLM

        llm_client = AgentLLM()
        console.print("[cyan]上下文增强已启用[/cyan]")

    def _ingest_one(filepath: str) -> IngestedDocument:
        run = _start_ingestion_run(namespace, filepath)
        started_at = time.perf_counter()
        try:
            artifacts = ingest_document(
                filepath,
                chunk_size=512,
                enable_contextual=settings.enable_contextual_retrieval,
                llm_client=llm_client,
            )
            stats = _persist_ingested_document(artifacts, namespace)
        except Exception as exc:
            _finish_ingestion_run(
                run,
                status="failed",
                documents_seen=0,
                chunks_written=0,
                error_message=str(exc),
            )
            raise

        _finish_ingestion_run(
            run,
            status="completed",
            documents_seen=1,
            chunks_written=stats["written_chunks"],
            metadata={
                "replaced_chunks": stats["replaced_chunks"],
                "duration_ms": round((time.perf_counter() - started_at) * 1000, 2),
            },
        )
        return artifacts

    path = os.path.abspath(path)
    if not os.path.exists(path):
        console.print(f"[red]路径不存在: {path}[/red]")
        return

    if os.path.isdir(path):
        for root, _, files in os.walk(path):
            for f in files:
                if f.endswith((".pdf", ".md", ".txt")):
                    try:
                        artifacts = _ingest_one(os.path.join(root, f))
                        console.print(f"  [green]+[/green] {f} ({len(artifacts.chunks)} 块)")
                    except Exception as e:
                        console.print(f"  [red]-[/red] {f}: {e}")
    else:
        artifacts = _ingest_one(path)
        console.print(f"[green]+[/green] {path} ({len(artifacts.chunks)} 块)")
    console.print(f"\n知识库共 [bold]{get_collection(namespace=namespace).count()}[/bold] 个文档块")


@kb_app.command("list")
def kb_list():
    """查看知识库状态"""
    settings = get_settings()
    catalog = get_knowledge_base_catalog()
    kb = catalog.get_knowledge_base(settings.rag_default_namespace)
    documents = catalog.list_documents(kb.kb_id) if kb else []
    runs = catalog.list_ingestion_runs(kb.kb_id)[:5] if kb else []
    console.print(
        f"知识库: [bold]{get_collection(namespace=settings.rag_default_namespace).count()}[/bold] 个文档块"
    )
    console.print(f"文档数: [bold]{len(documents)}[/bold]")
    if runs:
        latest = runs[0]
        console.print(
            f"最近导入: [bold]{latest.status}[/bold] {latest.source_uri} ({latest.chunks_written} 块)"
        )


@kb_app.command("search")
def kb_search_command(
    query: str = typer.Argument(..., help="检索问题"),
    top_k: int = typer.Option(5, "--top-k", min=1, max=20, help="返回结果数量"),
    source: str = typer.Option("", "--source", help="按 source_uri 过滤"),
    file_format: str = typer.Option("", "--format", help="按 format 过滤"),
    section_title: str = typer.Option("", "--section", help="按 section_title 过滤"),
    page_number: int | None = typer.Option(None, "--page", help="按页码过滤"),
):
    """搜索知识库"""
    settings = get_settings()
    namespace = settings.rag_default_namespace
    retriever = HybridRetriever(namespace=namespace)
    retriever.rebuild_from_catalog()
    if not retriever._chunks:
        console.print("[yellow]知识库为空[/yellow]")
        raise typer.Exit(code=0)

    if retriever._reranker is None:
        retriever.load_reranker()

    where = _build_search_where(
        source=source or None,
        file_format=file_format or None,
        section_title=section_title or None,
        page_number=page_number,
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
    results = retriever.search(query, dense, top_k=top_k, min_score=0.0)
    if not results:
        console.print("[yellow]未找到相关知识[/yellow]")
        raise typer.Exit(code=0)

    for index, item in enumerate(results, start=1):
        metadata = item.metadata or {}
        source_uri = metadata.get("source_uri", "")
        labels: list[str] = []
        if metadata.get("section_title"):
            labels.append(str(metadata["section_title"]))
        if metadata.get("page_number") is not None:
            labels.append(f"Page {metadata['page_number']}")
        suffix = f" [{' | '.join(labels)}]" if labels else ""
        console.print(f"[{index}] {source_uri}{suffix} score={item.score:.2f}")
        console.print(item.text)
