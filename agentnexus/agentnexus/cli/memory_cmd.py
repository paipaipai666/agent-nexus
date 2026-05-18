"""CLI memory list/clear commands"""
import typer
from rich.table import Table

from agentnexus.memory.long_term import LongTermMemory

from . import memory_app, console


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
        table.add_row(str(r["id"]), r["category"], f"{r['importance']:.1f}", r["content"])
    console.print(table)


@memory_app.command("clear")
def memory_clear():
    """清空长期记忆"""
    ltm = LongTermMemory()
    ltm.clear_all()
    console.print("[green]+[/green] 记忆已清空")
