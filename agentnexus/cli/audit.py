"""CLI audit commands - view tool call history and audit logs."""

import typer
from rich import box
from rich.table import Table

from agentnexus.observability.audit_log import (
    ThreadSafeAuditLog,
    append_audit,
    get_audit_log,
)

from . import app, console

__all__ = [
    "ThreadSafeAuditLog",
    "append_audit",
    "get_audit_log",
]


@app.command("audit")
def audit(
    limit: int = typer.Option(20, "--limit", "-n", help="Number of entries to show"),
    tool: str = typer.Option("", "--tool", "-t", help="Filter by tool name"),
):
    """Show tool-call audit logs."""
    from agentnexus.observability.audit_log import get_audit_log as _get_audit_log

    entries = _get_audit_log()
    if tool:
        entries = [e for e in entries if e.tool_name == tool]
    entries = entries[-limit:]

    if not entries:
        console.print("[dim]No audit records[/dim]")
        return

    table = Table(title="工具调用审计日志", box=box.ROUNDED)
    table.add_column("时间", style="dim")
    table.add_column("工具")
    table.add_column("调用者")
    table.add_column("结果")
    table.add_column("耗时(ms)", justify="right")
    table.add_column("HITL")
    table.add_column("错误")

    for e in entries:
        import datetime

        ts = datetime.datetime.fromtimestamp(e.timestamp).strftime("%H:%M:%S")
        table.add_row(
            ts,
            e.tool_name,
            e.caller,
            e.result_summary[:60],
            f"{e.duration_ms:.0f}",
            "✓" if e.hitl_triggered else "",
            e.error or "",
        )

    console.print(table)
