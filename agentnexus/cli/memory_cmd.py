"""CLI memory list/clear commands"""
import typer
from rich.table import Table

from . import console, memory_app


def get_long_term_memory():
    from agentnexus.memory.long_term import get_long_term_memory as _get_long_term_memory

    return _get_long_term_memory()


@memory_app.command("list")
def memory_list(limit: int = typer.Option(10, help="Number of entries")):
    """List long-term memories."""
    ltm = get_long_term_memory()
    rows = ltm.list_recent(limit)
    if not rows:
        console.print("[dim]No memories[/dim]")
        return
    table = Table(title="Long-term Memory")
    table.add_column("ID", style="dim")
    table.add_column("类别")
    table.add_column("重要性")
    table.add_column("内容")
    for r in rows:
        table.add_row(str(r["id"]), r["category"], f"{r['importance']:.1f}", r["content"])
    console.print(table)


@memory_app.command("clear")
def memory_clear():
    """Clear all long-term memories."""
    ltm = get_long_term_memory()
    ltm.clear_all()
    console.print("[green]+[/green] All memories cleared")
