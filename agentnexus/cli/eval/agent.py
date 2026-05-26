"""Agent-level eval CLI commands."""

import typer
from rich import box
from rich.table import Table

from agentnexus.cli import console, eval_app
from agentnexus.core.config import get_settings

# ── Agent evaluation (current architecture) ──────────────────────


@eval_app.command("agent")
def eval_agent(
    days: int = typer.Option(7, "--days", "-d", help="回溯天数"),
):
    """评估单 Agent 执行质量（从 JSONL trace 读取）"""
    from agentnexus.evaluation.agent_eval import AgentEvaluator

    traces_dir = get_settings().traces_dir
    evaluator = AgentEvaluator()
    report = evaluator.evaluate_all(traces_dir, days=days)

    if report.total_traces == 0:
        console.print("[dim]暂无可评估的 trace 数据。启动 nexus tui 执行一些对话后会生成 trace。[/dim]")
        return

    console.print(report.summary())

    if not report.tool_breakdown:
        console.print("\n[dim]无工具调用记录。[/dim]")
    else:
        tool_table = Table(title="工具调用明细", box=box.ROUNDED)
        tool_table.add_column("工具", style="cyan")
        tool_table.add_column("调用次数", justify="right")
        tool_table.add_column("错误数", justify="right")
        tool_table.add_column("成功率", justify="right")
        for name, info in sorted(report.tool_breakdown.items()):
            rate = info["success_rate"]
            rate_str = f"[green]{rate:.1%}[/green]" if rate >= 0.85 else f"[red]{rate:.1%}[/red]"
            tool_table.add_row(name, str(info["calls"]), str(info["errors"]), rate_str)
        console.print(tool_table)

    # Per-trace details
    if report.failed_traces:
        console.print("\n[bold yellow]异常 Trace:[/bold yellow]")
        for r in report.failed_traces[:5]:
            issues = []
            if r.had_error:
                issues.append("LLM 错误")
            if r.had_truncation:
                issues.append("上下文截断")
            if not r.had_answer:
                issues.append("未产出答案")
            console.print(f"  [{r.trace_id}] {r.task_preview[:60]} — {', '.join(issues)}")

    # CI gate
    if not report.passed:
        console.print("\n[bold yellow]⚠ 部分指标未达阈值[/bold yellow]")


