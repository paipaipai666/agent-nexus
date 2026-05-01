"""AgentNexus CLI"""
import typer
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

from agentnexus.rag.chroma_client import get_collection, insert_documents
from agentnexus.rag.ingestion import ingest, ChunkStrategy
from agentnexus.memory.long_term import LongTermMemory

app = typer.Typer(name="nexus", help="AgentNexus - 多智能体任务协同 CLI")
console = Console()

kb_app = typer.Typer(help="知识库管理")
app.add_typer(kb_app, name="kb")

memory_app = typer.Typer(help="记忆管理")
app.add_typer(memory_app, name="memory")


@app.command()
def run(task: str = typer.Argument(..., help="要执行的任务描述")):
    """执行一个任务"""
    from agentnexus.agents.multi_agent.orchestrator import orchestrator
    console.print(Panel(f"[bold]{task}[/bold]", title="任务"))
    result = orchestrator.invoke({"task": task})
    analysis = result.get("analysis", "")
    if analysis:
        console.print(Panel(analysis[:3000], title="结果", border_style="green"))
    console.print(f"评分: {result.get('critique_score', 'N/A')}  重试: {result.get('retry_count', 0)}")


@app.command()
def version():
    """显示版本"""
    console.print("[bold]AgentNexus[/bold] v0.1.0")


@kb_app.command("add")
def kb_add(path: str = typer.Argument(..., help="文档路径或目录")):
    """添加文档到知识库"""
    import os
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


@memory_app.command("list")
def memory_list(limit: int = typer.Option(10, help="显示条数")):
    """查看长期记忆"""
    ltm = LongTermMemory()
    rows = ltm.list_recent(limit)
    if not rows:
        console.print("[dim]暂无记忆[/dim]")
        return
    table = Table(title="长期记忆")
    table.add_column("ID", style="dim")
    table.add_column("类别")
    table.add_column("重要性")
    table.add_column("内容")
    for r in rows:
        table.add_row(str(r["id"]), r["category"], f"{r['importance']:.1f}", r["content"][:60])
    console.print(table)


@memory_app.command("clear")
def memory_clear():
    """清空长期记忆"""
    ltm = LongTermMemory()
    for m in ltm.list_recent(1000):
        ltm.delete(m["id"])
    console.print("[green]+[/green] 记忆已清空")
