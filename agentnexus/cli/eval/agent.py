"""Agent-level eval CLI commands."""

import typer
from rich import box
from rich.table import Table

from agentnexus.cli import console, eval_app
from agentnexus.core.config import get_settings

# ── Agent evaluation (current architecture) ──────────────────────


@eval_app.command("agent")
def eval_agent(
    days: int = typer.Option(7, "--days", "-d", help="Look back N days"),
):
    """Evaluate single-agent execution quality (from JSONL traces)."""
    from agentnexus.evaluation.agent_eval import AgentEvaluator

    traces_dir = get_settings().traces_dir
    evaluator = AgentEvaluator()
    report = evaluator.evaluate_all(traces_dir, days=days)

    if report.total_traces == 0:
        console.print("[dim]No trace data available for evaluation. Start nexus tui to generate traces.[/dim]")
        return

    console.print(report.summary())

    if not report.tool_breakdown:
        console.print("\n[dim]No tool call records.[/dim]")
    else:
        tool_table = Table(title="Tool Call Details", box=box.ROUNDED)
        tool_table.add_column("Tool", style="cyan")
        tool_table.add_column("Calls", justify="right")
        tool_table.add_column("Errors", justify="right")
        tool_table.add_column("Success Rate", justify="right")
        for name, info in sorted(report.tool_breakdown.items()):
            rate = info["success_rate"]
            rate_str = f"[green]{rate:.1%}[/green]" if rate >= 0.85 else f"[red]{rate:.1%}[/red]"
            tool_table.add_row(name, str(info["calls"]), str(info["errors"]), rate_str)
        console.print(tool_table)

    # Per-trace details
    if report.failed_traces:
        console.print("\n[bold yellow]Failed Traces:[/bold yellow]")
        for r in report.failed_traces[:5]:
            issues = []
            if r.had_error:
                issues.append("LLM error")
            if r.had_truncation:
                issues.append("context truncated")
            if not r.had_answer:
                issues.append("no answer produced")
            console.print(f"  [{r.trace_id}] {r.task_preview[:60]} — {', '.join(issues)}")

    # CI gate
    if not report.passed:
        console.print("\n[bold yellow]⚠ Some metrics below threshold[/bold yellow]")


