"""CLI stats command"""
import typer
from rich import box
from rich.panel import Panel
from rich.table import Table

from . import app, console


@app.command()
def stats(days: int = typer.Option(7, "--days", "-d", help="统计最近 N 天")):
    """显示 Token 成本统计"""
    from agentnexus.observability.stats import compute_stats
    from agentnexus.core.config import get_settings

    s = compute_stats(get_settings().traces_dir, days)

    if s.total_tasks == 0:
        console.print(f"[dim]最近 {days} 天暂无任务数据[/dim]")
        return

    summary_lines = [
        f"[bold]总任务数:[/bold] {s.total_tasks}",
        f"[bold]平均重试:[/bold] {s.avg_retries} 次/任务",
        f"[bold]总 Token:[/bold] 输入 {s.total_input_tokens:,} / 输出 {s.total_output_tokens:,}",
        f"[bold]总成本:[/bold] CNY {s.total_cost_cny:.4f}",
        f"[bold]延迟:[/bold] 平均 {s.avg_latency_ms}ms | P95 {s.p95_latency_ms}ms | 最大 {s.max_latency_ms}ms",
    ]
    if s.total_tasks > 0:
        summary_lines.append(f"[bold]预估 CNY/任务:[/bold] CNY {s.total_cost_cny / s.total_tasks:.4f}")

    console.print(Panel(
        "\n".join(summary_lines),
        title=f"Token 成本统计（最近 {days} 天）",
        border_style="cyan",
    ))

    if s.by_model:
        model_table = Table(title="按模型分布", box=box.ROUNDED)
        model_table.add_column("模型", style="cyan")
        model_table.add_column("任务数", justify="right")
        model_table.add_column("输入 Token", justify="right")
        model_table.add_column("输出 Token", justify="right")
        model_table.add_column("成本 (CNY)", justify="right")
        model_table.add_column("占比", justify="right")

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
        date_table = Table(title="按日期趋势", box=box.ROUNDED)
        date_table.add_column("日期", style="cyan")
        date_table.add_column("模型")
        date_table.add_column("任务数", justify="right")
        date_table.add_column("输入 Token", justify="right")
        date_table.add_column("输出 Token", justify="right")

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
