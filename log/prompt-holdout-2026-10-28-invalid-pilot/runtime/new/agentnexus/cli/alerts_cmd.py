"""CLI alerts command — 查看告警历史"""

import logging

import typer

logger = logging.getLogger(__name__)
from rich import box
from rich.table import Table

from . import app, console


@app.command()
def alerts(
    days: int = typer.Option(7, "--days", "-d", help="显示最近 N 天的告警"),
    severity: str = typer.Option(None, "--severity", "-s", help="按级别过滤：info/warning/critical"),
):
    """显示告警历史。"""
    from agentnexus.observability.alerting import get_alert_manager

    manager = get_alert_manager()
    alert_list = manager.get_history(days=days, severity=severity)

    if not alert_list:
        console.print(f"[dim]No alerts in the last {days} days[/dim]")
        return

    table = Table(title=f"Alerts (Last {days} Days)", box=box.ROUNDED)
    table.add_column("Time", style="cyan")
    table.add_column("Severity")
    table.add_column("Type")
    table.add_column("Message")

    severity_styles = {
        "info": "blue",
        "warning": "yellow",
        "critical": "red bold",
    }

    for a in alert_list:
        import time as _time
        ts = _time.strftime("%Y-%m-%d %H:%M", _time.localtime(a.timestamp))
        style = severity_styles.get(a.severity.value, "white")
        table.add_row(
            ts,
            f"[{style}]{a.severity.value.upper()}[/{style}]",
            a.alert_type.value,
            a.message[:80],
        )

    console.print(table)
