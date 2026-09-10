"""CLI memory list/clear commands"""
import typer
from rich.table import Table

from . import console, memory_app


def get_long_term_memory():
    from agentnexus.memory.long_term import get_long_term_memory as _get_long_term_memory

    return _get_long_term_memory()


@memory_app.command("list")
def memory_list(limit: int = typer.Option(10, help="显示的条目数")):
    """列出长期记忆。"""
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
    """清空所有长期记忆。"""
    ltm = get_long_term_memory()
    ltm.clear_all()
    console.print("[green]+[/green] All memories cleared")


@memory_app.command("project")
def memory_project():
    """显示当前项目的 .agentnexus/ 项目记忆索引与状态。"""
    from pathlib import Path

    from agentnexus.memory.project import ProjectMemory

    pm = ProjectMemory(Path.cwd())
    console.print(f"[dim]{pm.root}[/dim]")
    ctx = pm.format_context()
    if not ctx:
        console.print("[dim]项目记忆为空（.agentnexus/ 目录已就绪）[/dim]")
        return
    console.print(ctx, markup=False)


@memory_app.command("pending")
def memory_pending(limit: int = typer.Option(20, help="显示的条目数")):
    """列出待确认的记忆提案（curator 提炼，未批准前不入库）。"""
    ltm = get_long_term_memory()
    rows = ltm.list_pending(limit=limit)
    if not rows:
        console.print("[dim]No pending memories[/dim]")
        return
    table = Table(title="Pending Memories (quarantine)")
    table.add_column("ID", style="dim")
    table.add_column("范围")
    table.add_column("类别/Kind")
    table.add_column("内容")
    for r in rows:
        kind_or_cat = r["kind"] if r["scope"] == "project" else r["category"]
        console_text = f"{r['content']}"
        if r["scope"] == "project" and r["workspace_path"]:
            console_text += f" [dim]({r['workspace_path']})[/dim]"
        table.add_row(str(r["id"]), r["scope"], kind_or_cat, console_text)
    console.print(table)
    console.print("[dim]批准: memory approve <id> ｜ 拒绝: memory reject <id>[/dim]")


@memory_app.command("approve")
def memory_approve(pending_id: int):
    """批准一条待确认记忆：user 级入长期记忆，project 级写入该项目 .agentnexus/。"""
    ltm = get_long_term_memory()
    result = ltm.approve_pending(pending_id)
    if result.get("status") == "error":
        console.print(f"[red]批准失败:[/red] {result.get('error')}")
        raise typer.Exit(1)
    target = result.get("memory_id") or result.get("target", "")
    console.print(f"[green]+[/green] Approved ({result['scope']}) {target}")


@memory_app.command("reject")
def memory_reject(pending_id: int):
    """拒绝一条待确认记忆（保留记录供审计）。"""
    ltm = get_long_term_memory()
    if ltm.get_pending(pending_id) is None:
        console.print(f"[red]Not found:[/red] pending id {pending_id}")
        raise typer.Exit(1)
    ltm.set_pending_status(pending_id, "rejected")
    console.print(f"[green]+[/green] Rejected pending #{pending_id}")


@memory_app.command("stats")
def memory_stats():
    """记忆库健康指标：总量、从未被检索比例、待确认数。"""
    ltm = get_long_term_memory()
    s = ltm.stats()
    console.print(f"总记忆数: {s['total']}  (按类别: {s['by_category']})")
    console.print(f"从未被检索: {s['never_accessed']} ({s['never_accessed_rate']:.0%})")
    console.print(f"已被取代: {s['superseded']}  待确认: {s['pending']}")
