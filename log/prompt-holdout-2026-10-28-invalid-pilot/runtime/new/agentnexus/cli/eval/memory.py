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


@eval_app.command("memory-audit")
def eval_memory_audit(
    db: str | None = typer.Option(None, "--db", help="memory.db 路径（默认取配置 memory_db_path）"),
    sample_csv: str | None = typer.Option(
        None, "--sample-csv", help="导出分层抽样 CSV 供标注，计算线上 P_write"),
    n_per_category: int = typer.Option(10, "--n-per-cat", help="每类抽样条数"),
):
    """审计真实 LTM 库：类别/写入者分布、强信号占比、标注采样（GateCal P3，只读）。"""
    from agentnexus.evaluation.audit_ltm import main as audit_main

    audit_main(db_path=db, sample_csv=sample_csv, n_per_category=n_per_category)
