"""CLI kb add/list commands"""
import os

import typer

from agentnexus.core.config import get_settings
from agentnexus.rag.chroma_client import (
    chunk_metadata_to_chroma,
    get_collection,
    resolve_collection_name,
    upsert_documents,
)
from agentnexus.rag.ingestion import ingest_document
from agentnexus.rag.models import IngestedDocument, KnowledgeBaseRecord
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


def _persist_ingested_document(artifacts: IngestedDocument, namespace: str):
    kb_record = _default_kb_record(namespace)
    catalog = get_knowledge_base_catalog()
    catalog.upsert_knowledge_base(kb_record)

    artifacts.document.kb_id = kb_record.kb_id
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
        artifacts = ingest_document(
            filepath,
            chunk_size=512,
            enable_contextual=settings.enable_contextual_retrieval,
            llm_client=llm_client,
        )
        _persist_ingested_document(artifacts, namespace)
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
    console.print(
        f"知识库: [bold]{get_collection(namespace=settings.rag_default_namespace).count()}[/bold] 个文档块"
    )
