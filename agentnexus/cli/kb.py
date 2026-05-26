"""CLI kb add/list/search commands"""
import os
import time

import typer

from agentnexus.core.config import get_settings
from agentnexus.rag.models import IngestedDocument, IngestionRunRecord, KnowledgeBaseRecord

from . import console, kb_app


def ingest_document(*args, **kwargs):
    from agentnexus.rag.ingestion import ingest_document as _ingest_document

    return _ingest_document(*args, **kwargs)


def delete_documents(*args, **kwargs):
    from agentnexus.storage.chroma import delete_documents as _delete_documents

    return _delete_documents(*args, **kwargs)


def upsert_documents(*args, **kwargs):
    from agentnexus.storage.chroma import upsert_documents as _upsert_documents

    return _upsert_documents(*args, **kwargs)


def get_collection(*args, **kwargs):
    from agentnexus.storage.chroma import get_collection as _get_collection

    return _get_collection(*args, **kwargs)


def chroma_search(*args, **kwargs):
    from agentnexus.storage.chroma import search as _chroma_search

    return _chroma_search(*args, **kwargs)


def expand_queries(*args, **kwargs):
    from agentnexus.rag.retriever import expand_queries as _expand_queries

    return _expand_queries(*args, **kwargs)


def result_citation(*args, **kwargs):
    from agentnexus.rag.retriever import result_citation as _result_citation

    return _result_citation(*args, **kwargs)


def result_display_text(*args, **kwargs):
    from agentnexus.rag.retriever import result_display_text as _result_display_text

    return _result_display_text(*args, **kwargs)


def HybridRetriever(*args, **kwargs):
    from agentnexus.rag.retriever import HybridRetriever as _HybridRetriever

    return _HybridRetriever(*args, **kwargs)


def get_knowledge_base_catalog(*args, **kwargs):
    from agentnexus.rag.store import get_knowledge_base_catalog as _get_knowledge_base_catalog

    return _get_knowledge_base_catalog(*args, **kwargs)


def _build_search_where(*args, **kwargs):
    from agentnexus.tools.kb_search import _build_search_where as _build_where

    return _build_where(*args, **kwargs)


def _default_kb_record(namespace: str) -> KnowledgeBaseRecord:
    from agentnexus.rag.kb_service import default_kb_record

    return default_kb_record(namespace)


def _delete_existing_source_versions(namespace: str, source_id: str) -> int:
    from agentnexus.rag import kb_service
    from agentnexus.rag.kb_service import delete_existing_source_versions

    kb_service.delete_documents = delete_documents
    return delete_existing_source_versions(namespace, source_id)


def _persist_ingested_document(artifacts: IngestedDocument, namespace: str) -> dict[str, int]:
    from agentnexus.rag import kb_service
    from agentnexus.rag.kb_service import persist_ingested_document

    kb_service.delete_documents = delete_documents
    kb_service.upsert_documents = upsert_documents
    return persist_ingested_document(artifacts, namespace)


def _start_ingestion_run(namespace: str, source_uri: str) -> IngestionRunRecord:
    from agentnexus.rag.kb_service import start_ingestion_run

    return start_ingestion_run(namespace, source_uri)


def _finish_ingestion_run(
    run: IngestionRunRecord,
    *,
    status: str,
    documents_seen: int,
    chunks_written: int,
    error_message: str = "",
    metadata: dict | None = None,
):
    from agentnexus.rag.kb_service import finish_ingestion_run

    finish_ingestion_run(
        run,
        status=status,
        documents_seen=documents_seen,
        chunks_written=chunks_written,
        error_message=error_message,
        metadata=metadata,
    )


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
                if f.endswith((".pdf", ".md", ".txt", ".html", ".htm", ".json", ".docx", ".xlsx")):
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
    view: str = typer.Option("section", "--view", help="结果视图: section 或 chunk"),
    source: str = typer.Option("", "--source", help="按 source_uri 过滤"),
    file_format: str = typer.Option("", "--format", help="按 format 过滤"),
    section_title: str = typer.Option("", "--section", help="按 section_title 过滤"),
    page_number: int | None = typer.Option(None, "--page", help="按页码过滤"),
    block_type: str = typer.Option("", "--block-type", help="按块类型过滤: paragraph/list/heading/code"),
    has_code: bool | None = typer.Option(None, "--has-code/--no-code", help="过滤是否包含代码"),
    has_list: bool | None = typer.Option(None, "--has-list/--no-list", help="过滤是否包含列表"),
    heading_depth: int | None = typer.Option(None, "--heading-depth", min=1, help="按标题层级过滤"),
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
        console.print("[yellow]未找到相关知识[/yellow]")
        raise typer.Exit(code=0)
    results = retriever.expand_contexts(results, view=view)

    for index, item in enumerate(results, start=1):
        console.print(f"[{index}] {result_citation(item)} score={item.score:.2f}")
        console.print(result_display_text(item))
