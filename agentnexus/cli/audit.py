"""CLI audit commands - view tool call history and audit logs."""

from collections.abc import Iterator
from threading import RLock

import typer
from rich import box
from rich.table import Table

from agentnexus.tools.registry import AuditEntry

from . import app, console


class ThreadSafeAuditLog:
    """Small list-like audit buffer guarded by a re-entrant lock."""

    def __init__(self):
        self._entries: list[AuditEntry] = []
        self._lock = RLock()

    def append(self, entry: AuditEntry) -> None:
        with self._lock:
            self._entries.append(entry)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def copy(self) -> list[AuditEntry]:
        with self._lock:
            return list(self._entries)

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def __iter__(self) -> Iterator[AuditEntry]:
        return iter(self.copy())

    def __getitem__(self, key):
        with self._lock:
            if isinstance(key, slice):
                return list(self._entries[key])
            return self._entries[key]


# Store audit entries globally for CLI access.
_global_audit_log = ThreadSafeAuditLog()


def get_audit_log() -> list[AuditEntry]:
    return _global_audit_log.copy()


def append_audit(entry: AuditEntry) -> None:
    _global_audit_log.append(entry)


@app.command("audit")
def audit(
    limit: int = typer.Option(20, "--limit", "-n", help="Number of entries to show"),
    tool: str = typer.Option("", "--tool", "-t", help="Filter by tool name"),
):
    """Show tool-call audit logs."""
    entries = get_audit_log()
    if tool:
        entries = [e for e in entries if e.tool_name == tool]
    entries = entries[-limit:]

    if not entries:
        console.print("[dim]暂无审计记录[/dim]")
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
