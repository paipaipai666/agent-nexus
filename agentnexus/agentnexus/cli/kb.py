"""CLI kb add/list commands"""
import os
import typer

from agentnexus.rag.chroma_client import get_collection, insert_documents
from agentnexus.rag.ingestion import ingest
from agentnexus.core.config import get_settings

from . import kb_app, console


@kb_app.command("add")
def kb_add(path: str = typer.Argument(..., help="文档路径或目录")):
    """添加文档到知识库"""
    settings = get_settings()
    llm_client = None
    if settings.enable_contextual_retrieval:
        from agentnexus.core.llm import AgentLLM
        llm_client = AgentLLM()
        console.print("[cyan]上下文增强已启用[/cyan]")

    def _ingest_one(filepath):
        return ingest(
            filepath, chunk_size=512,
            enable_contextual=settings.enable_contextual_retrieval,
            llm_client=llm_client,
        )

    path = os.path.abspath(path)
    if not os.path.exists(path):
        console.print(f"[red]路径不存在: {path}[/red]")
        return

    if os.path.isdir(path):
        for root, _, files in os.walk(path):
            for f in files:
                if f.endswith((".pdf", ".md", ".txt")):
                    try:
                        chunks = _ingest_one(os.path.join(root, f))
                        insert_documents(chunks)
                        console.print(f"  [green]+[/green] {f} ({len(chunks)} 块)")
                    except Exception as e:
                        console.print(f"  [red]-[/red] {f}: {e}")
    else:
        chunks = _ingest_one(path)
        insert_documents(chunks)
        console.print(f"[green]+[/green] {path} ({len(chunks)} 块)")
    console.print(f"\n知识库共 [bold]{get_collection().count()}[/bold] 个文档块")


@kb_app.command("list")
def kb_list():
    """查看知识库状态"""
    console.print(f"知识库: [bold]{get_collection().count()}[/bold] 个文档块")
