"""CLI stats command"""
import logging

import typer

logger = logging.getLogger(__name__)
from rich import box
from rich.panel import Panel
from rich.table import Table

from . import app, console


@app.command()
def stats(days: int = typer.Option(7, "--days", "-d", help="Statistics for the last N days")):
    """Show token cost statistics."""
    from agentnexus.core.config import get_settings
    from agentnexus.observability.stats import compute_stats

    try:
        s = compute_stats(get_settings().traces_dir, days)
    except Exception as e:
        logger.exception("Failed to compute statistics")
        console.print(f"[red]Failed to compute statistics. Check that trace files are valid.[/red]")
        raise SystemExit(1)

    if s.total_tasks == 0:
        console.print(f"[dim]No task data in the last {days} days[/dim]")
        return

    summary_lines = [
        f"[bold]Total tasks:[/bold] {s.total_tasks}",
        f"[bold]Avg retries:[/bold] {s.avg_retries} per task",
        f"[bold]Total tokens:[/bold] input {s.total_input_tokens:,} / output {s.total_output_tokens:,}",
        f"[bold]Total cost:[/bold] CNY {s.total_cost_cny:.4f}",
        f"[bold]Latency:[/bold] avg {s.avg_latency_ms}ms | P95 {s.p95_latency_ms}ms | max {s.max_latency_ms}ms",
    ]
    if s.total_tasks > 0:
        summary_lines.append(f"[bold]Est. CNY/task:[/bold] CNY {s.total_cost_cny / s.total_tasks:.4f}")

    # Prompt cache statistics
    if s.total_cache_hit_tokens + s.total_cache_miss_tokens > 0:
        summary_lines.append("")
        summary_lines.append(f"[bold]Prompt cache hit rate:[/bold] {s.cache_hit_rate:.1%}")
        total_cache = s.total_cache_hit_tokens + s.total_cache_miss_tokens
        summary_lines.append(
            f"[bold]Cache hit tokens:[/bold] {s.total_cache_hit_tokens:,} / {total_cache:,}"
        )
        if s.cache_saved_cost_cny > 0:
            summary_lines.append(f"[bold]Cache saved cost:[/bold] CNY {s.cache_saved_cost_cny:.4f}")

    console.print(Panel(
        "\n".join(summary_lines),
        title=f"Token Cost Statistics (Last {days} Days)",
        border_style="cyan",
    ))

    if s.by_model:
        model_table = Table(title="By Model", box=box.ROUNDED)
        model_table.add_column("Model", style="cyan")
        model_table.add_column("Tasks", justify="right")
        model_table.add_column("Input Token", justify="right")
        model_table.add_column("Output Token", justify="right")
        model_table.add_column("Cost (CNY)", justify="right")
        model_table.add_column("Share", justify="right")

        for model, info in s.by_model.items():
            pct = (info["cost_cny"] / s.total_cost_cny * 100) if s.total_cost_cny > 0 else 0
            model_table.add_row(
                model,
                str(info["tasks"]),
                f"{info['input_tokens']:,}",
                f"{info['output_tokens']:,}",
                f"CNY {info['cost_cny']:.4f}",
                f"{pct:.1f}%",
            )
        console.print(model_table)

    if s.by_date:
        date_table = Table(title="By Date Trend", box=box.ROUNDED)
        date_table.add_column("Date", style="cyan")
        date_table.add_column("Model")
        date_table.add_column("Tasks", justify="right")
        date_table.add_column("Input Token", justify="right")
        date_table.add_column("Output Token", justify="right")

        for date_str, models in s.by_date.items():
            for model, info in models.items():
                date_table.add_row(
                    date_str,
                    model,
                    str(info.get("tasks", 0)),
                    f"{info.get('input', 0):,}",
                    f"{info.get('output', 0):,}",
                )
        console.print(date_table)
