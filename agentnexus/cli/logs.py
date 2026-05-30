"""CLI logs list/view commands"""
import json
import time
from datetime import datetime
from pathlib import Path

import typer
from rich import box
from rich.panel import Panel
from rich.table import Table
from rich.tree import Tree

from agentnexus.core.config import get_settings

from . import console, logs_app


def _read_trace_spans(days: int):
    """Read spans from JSONL trace files, filtered by days."""
    traces_dir = Path(get_settings().traces_dir)
    if not traces_dir.exists():
        return []

    cutoff = time.time() - days * 86400
    all_spans: list[dict] = []

    jsonl_files = sorted(traces_dir.glob("*.jsonl"), reverse=True)
    for f in jsonl_files:
        try:
            with open(f, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        span = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if span.get("start_time", 0) >= cutoff:
                        all_spans.append(span)
        except Exception:
            continue

    return all_spans


@logs_app.command("list")
def logs_list(days: int = typer.Option(7, "--days", "-d", help="Look back N days of traces")):
    """List historical trace records."""
    spans = _read_trace_spans(days)

    if not spans:
        console.print(f"[dim]No trace records in the last {days} days[/dim]")
        return

    traces: dict[str, dict] = {}
    for span in spans:
        tid = span.get("trace_id", "unknown")
        if tid not in traces:
            traces[tid] = {
                "span_count": 0,
                "total_tokens": 0,
                "first_time": float("inf"),
                "last_time": 0.0,
                "status": "ok",
            }
        info = traces[tid]
        info["span_count"] += 1
        meta = span.get("metadata", {})
        info["total_tokens"] += meta.get("input_tokens", 0) + meta.get("output_tokens", 0)
        st = span.get("start_time", 0)
        info["first_time"] = min(info["first_time"], st)
        et = span.get("end_time", 0)
        info["last_time"] = max(info["last_time"], et)
        if span.get("metadata", {}).get("status") == "error":
            info["status"] = "error"

    if not traces:
        console.print(f"[dim]No trace records in the last {days} days[/dim]")
        return

    table = Table(title=f"Historical Traces (Last {days} Days)", box=box.ROUNDED)
    table.add_column("Trace ID", style="cyan")
    table.add_column("Time", style="dim")
    table.add_column("Spans", justify="right")
    table.add_column("Token", justify="right")
    table.add_column("Latency(ms)", justify="right")
    table.add_column("Status")

    for tid, info in sorted(traces.items(),
                             key=lambda x: x[1]["first_time"], reverse=True):
        date_str = ""
        if info["first_time"] != float("inf"):
            date_str = datetime.fromtimestamp(info["first_time"]).strftime("%m-%d %H:%M")
        latency = round(info["last_time"] - info["first_time"], 1) * 1000 if info["first_time"] != float("inf") else 0
        status_icon = "[green]✓[/green]" if info["status"] == "ok" else "[red]✗[/red]"
        table.add_row(
            tid,
            date_str,
            str(info["span_count"]),
            str(info["total_tokens"]),
            f"{latency:.0f}",
            status_icon,
        )

    console.print(table)


@logs_app.command("view")
def logs_view(trace_id: str = typer.Option(..., "--trace-id", "-t", help="Trace ID to view")):
    """View the complete span tree for a given trace."""
    traces_dir = Path(get_settings().traces_dir)
    if not traces_dir.exists():
        console.print("[dim]No trace records[/dim]")
        return

    spans: list[dict] = []
    for f in sorted(traces_dir.glob("*.jsonl")):
        try:
            with open(f, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        span = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if span.get("trace_id") == trace_id:
                        spans.append(span)
        except Exception:
            continue

    if not spans:
        console.print(f"[red]Trace not found: {trace_id}[/red]")
        return

    span_map: dict[str, dict] = {s["span_id"]: s for s in spans}
    children_map: dict[str, list[dict]] = {}
    roots: list[dict] = []

    for s in spans:
        pid = s.get("parent_span_id", "")
        if pid and pid in span_map:
            children_map.setdefault(pid, []).append(s)
        else:
            roots.append(s)

    def _build_tree(span: dict, tree: Tree | None = None) -> Tree:
        name = span.get("name", "unknown")
        latency = span.get("latency_ms", 0)
        meta = span.get("metadata", {})
        model = meta.get("model", "")
        tokens_in = meta.get("input_tokens", 0)
        tokens_out = meta.get("output_tokens", 0)
        status = meta.get("status", "ok")
        status_color = "red" if status == "error" else "green"

        details: list[str] = []
        if latency:
            details.append(f"{latency}ms")
        if model:
            details.append(model)
        if tokens_in or tokens_out:
            details.append(f"{tokens_in}+{tokens_out} tok")

        label = f"[bold]{name}[/bold]"
        if details:
            label += f" [dim]({', '.join(details)})[/dim]"
        label += f" [{status_color}]●[/{status_color}]"

        node = tree.add(label) if tree else Tree(label)

        for child in children_map.get(span["span_id"], []):
            _build_tree(child, node)

        return node

    root_tree: Tree | None = None
    for root in roots:
        root_tree = _build_tree(root)

    if root_tree:
        console.print(Panel(f"Trace [bold cyan]{trace_id}[/bold cyan]", title="Trace Details"))
        console.print(root_tree)

        # Summary section
        total_latency = sum(s.get("latency_ms", 0) for s in spans)
        total_in = sum(s.get("metadata", {}).get("input_tokens", 0) for s in spans)
        total_out = sum(s.get("metadata", {}).get("output_tokens", 0) for s in spans)
        error_count = sum(1 for s in spans if s.get("metadata", {}).get("status") == "error")

        summary_lines = [
            f"Total spans: {len(spans)}",
            f"Total latency: {total_latency:.1f}ms",
            f"Total tokens: input {total_in} / output {total_out}",
        ]
        if error_count:
            summary_lines.append(f"[red]Errors: {error_count} spans[/red]")

        console.print(Panel("\n".join(summary_lines), title="Summary", border_style="dim"))
