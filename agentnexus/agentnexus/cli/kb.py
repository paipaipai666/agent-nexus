"""CLI kb add/list commands"""
import os
import typer

from agentnexus.rag.chroma_client import get_collection, insert_documents
from agentnexus.rag.ingestion import ingest

from . import kb_app, console


@kb_app.command("add")
def kb_add(path: str = typer.Argument(..., help="文档路径或目录")):
    """添加文档到知识库"""
    if os.path.isdir(path):
        for root, _, files in os.walk(path):
            for f in files:
                if f.endswith((".pdf", ".md", ".txt")):
                    try:
                        chunks = ingest(os.path.join(root, f), chunk_size=512)
                        insert_documents(chunks)
                        console.print(f"  [green]+[/green] {f} ({len(chunks)} 块)")
                    except Exception as e:
                        console.print(f"  [red]-[/red] {f}: {e}")
    else:
        chunks = ingest(path)
        insert_documents(chunks)
        console.print(f"[green]+[/green] {path} ({len(chunks)} 块)")
    console.print(f"\n知识库共 [bold]{get_collection().count()}[/bold] 个文档块")


@kb_app.command("list")
def kb_list():
    """查看知识库状态"""
    console.print(f"知识库: [bold]{get_collection().count()}[/bold] 个文档块")
