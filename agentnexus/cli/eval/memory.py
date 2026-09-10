"""Memory-system eval CLI commands."""

import typer
from rich import box
from rich.table import Table

from agentnexus.cli import console, eval_app


@eval_app.command("memory")
def eval_memory(
    ci: bool = typer.Option(False, "--ci", help="探针失败时以非零码退出（CI 门禁）"),
    judge: bool = typer.Option(False, "--judge", help="启用 LLM judge 质量探针（需要真实模型配置）"),
):
    """记忆系统评测：LTM / STM / 项目记忆的确定性探针（无需 LLM，离线可跑）。"""
    from agentnexus.evaluation.memory_eval import MemoryEvaluator

    report = MemoryEvaluator(enable_judge=judge).run()
    console.print(report.summary())

    table = Table(title="Probe Details", box=box.ROUNDED)
    table.add_column("Probe", style="cyan")
    table.add_column("Layer")
    table.add_column("Dimension")
    table.add_column("Result", justify="right")
    for r in report.results:
        mark = "[green]PASS[/green]" if r.passed else "[red]FAIL[/red]"
        table.add_row(r.name, r.layer, r.dimension, mark)
    console.print(table)

    if not report.passed and ci:
        raise typer.Exit(1)
