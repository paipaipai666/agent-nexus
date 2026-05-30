"""Trace-based eval CLI commands."""

import typer
from rich import box
from rich.table import Table

from agentnexus.cli import console, eval_app
from agentnexus.core.config import get_settings


@eval_app.command("trajectory")
def eval_trajectory(
    trace_id: str = typer.Option("", "--trace-id", "-t", help="Trace ID to evaluate (omit for all)"),
    days: int = typer.Option(7, "--days", "-d", help="Look back N days"),
):
    """Run trajectory quality evaluation (deterministic rules, no LLM-as-Judge)."""
    from agentnexus.core.config import get_settings
    from agentnexus.evaluation.trajectory import TrajectoryEvaluator

    traces_dir = get_settings().traces_dir
    evaluator = TrajectoryEvaluator()

    if trace_id:
        report = evaluator.evaluate_trace(trace_id, traces_dir)
        if report is None:
            console.print(f"[red]Trace not found: {trace_id}[/red]")
            return
        reports = [report]
    else:
        reports = evaluator.evaluate_all(traces_dir)

    if not reports:
        console.print("[dim]No trace data available for evaluation[/dim]")
        return

    table = Table(title="Trajectory Evaluation Results", box=box.ROUNDED)
    table.add_column("Trace ID", style="cyan")
    table.add_column("Spans", justify="right")
    table.add_column("Score", justify="right")
    table.add_column("Issues", justify="right")
    table.add_column("Verdict")

    passed = 0
    for r in reports:
        icon = "[green]PASS[/green]" if r.passed else "[red]FAIL[/red]"
        if r.passed:
            passed += 1
        table.add_row(
            r.trace_id, str(r.total_spans),
            f"{r.score:.1f}", str(r.issue_count), icon
        )

    console.print(table)
    console.print(f"Passed: {passed}/{len(reports)}")

    # Show details for failed traces
    for r in reports:
        if not r.passed:
            console.print(f"\n[bold red]FAIL[/bold red] {r.trace_id} (score={r.score:.1f})")
            for issue in r.issues:
                console.print(f"  [dim]{issue.check}: {issue.detail}[/dim]")


@eval_app.command("ci")
def eval_ci(
    days: int = typer.Option(1, "--days", "-d", help="Look back N days"),
):
    """CI mode: single-agent quality evaluation, exit(1) on failure."""
    from agentnexus.evaluation.agent_eval import AgentEvaluator

    evaluator = AgentEvaluator()
    report = evaluator.evaluate_all(get_settings().traces_dir, days=days)

    if report.total_traces == 0:
        console.print("[dim]No traces to evaluate[/dim]")
        return

    console.print(report.summary())
    if report.failed_traces:
        console.print(f"\n[red]{len(report.failed_traces)}/{report.total_traces} traces failed[/red]")
    if not report.passed:
        raise typer.Exit(code=1)


@eval_app.command("component")
def eval_component():
    """Run component-level evaluation (Coder/Researcher/Executor/Analyst)."""
    from agentnexus.core.config import get_settings
    from agentnexus.evaluation.component import ComponentEvaluator

    evaluator = ComponentEvaluator()
    report = evaluator.evaluate_all(get_settings().traces_dir)

    if report.total_traces == 0:
        console.print("[dim]No trace data available for evaluation[/dim]")
        return

    table = Table(title="Component Evaluation Results", box=box.ROUNDED)
    table.add_column("Agent", style="cyan")
    table.add_column("Score", justify="right")
    table.add_column("Checks", justify="right")

    for agent, info in sorted(report.by_agent.items()):
        table.add_row(agent, f"{info['score']:.1f}", str(info["count"]))

    console.print(table)
    console.print(f"Total traces: {report.total_traces} | Issues: {report.issue_count}")

    if report.by_tool:
        console.print()
        tool_table = Table(title="Tool Execution Success Rate", box=box.ROUNDED)
        tool_table.add_column("Tool", style="cyan")
        tool_table.add_column("Success Rate", justify="right")
        tool_table.add_column("Success/Total")
        for tool, ts in sorted(report.by_tool.items()):
            rate = ts["success"] / ts["total"] if ts["total"] else 0
            tool_table.add_row(tool, f"{rate:.1%}", f"{ts['success']}/{ts['total']}")
        console.print(tool_table)

    if report.issues:
        console.print()
        for issue in report.issues[:10]:
            console.print(f"  [[{issue.severity.upper()}]] {issue.agent}: {issue.detail}")


@eval_app.command("hallucination")
def eval_hallucination(
    trace_id: str = typer.Option("", "--trace-id", "-t", help="Trace ID to evaluate (omit for all)"),
):
    """Hallucination detection: extract claims from answers and verify against context."""
    from agentnexus.core.config import get_settings
    from agentnexus.evaluation.hallucination import HallucinationDetector

    detector = HallucinationDetector()
    traces_dir = get_settings().traces_dir

    if trace_id:
        report = detector.evaluate_trace(trace_id, traces_dir)
        if report is None:
            console.print(f"[red]Trace not found: {trace_id}[/red]")
            return
        reports = [report]
    else:
        reports = detector.evaluate_all(traces_dir)

    if not reports:
        console.print("[dim]No evaluation data available[/dim]")
        return

    table = Table(title="Hallucination Detection", box=box.ROUNDED)
    table.add_column("Trace ID", style="cyan")
    table.add_column("Claims", justify="right")
    table.add_column("Unsupported", justify="right")
    table.add_column("Hallucination Rate", justify="right")
    table.add_column("Verdict")

    total_claims = 0
    total_unsupported = 0
    for r in reports:
        icon = "[green]PASS[/green]" if r.passed else "[red]FAIL[/red]"
        table.add_row(r.trace_id, str(r.total_claims), str(r.unsupported_claims),
                      f"{r.hallucination_rate:.1%}", icon)
        total_claims += r.total_claims
        total_unsupported += r.unsupported_claims

    console.print(table)
    overall = total_unsupported / total_claims if total_claims else 0
    console.print(f"\nOverall hallucination rate: {overall:.1%} | Total claims: {total_claims} | Unsupported: {total_unsupported}")

    for r in reports:
        if not r.passed and r.flagged_claims:
            console.print(f"\n[bold red]FAIL {r.trace_id}[/bold red]")
            for c in r.flagged_claims[:3]:
                console.print(f"  [dim]⚠ {c}[/dim]")


@eval_app.command("tool-selection")
def eval_tool_selection():
    """Tool selection accuracy: compare actual vs expected tool choices."""
    from agentnexus.core.config import get_settings
    from agentnexus.evaluation.tool_selection import ToolSelectionEvaluator

    evaluator = ToolSelectionEvaluator()
    report = evaluator.evaluate_from_traces(get_settings().traces_dir)

    if report.total_queries == 0:
        console.print("[dim]No evaluation data available[/dim]")
        return

    icon = "[green]PASS[/green]" if report.passed else "[red]FAIL[/red]"
    console.print(f"[bold]Tool selection accuracy:[/bold] {report.accuracy:.1%} {icon}")
    console.print(f"Correct: {report.correct}/{report.total_queries}")
    console.print()

    table = Table(title="By Tool Breakdown", box=box.ROUNDED)
    table.add_column("Tool", style="cyan")
    table.add_column("Accuracy", justify="right")
    table.add_column("Correct/Total")
    for tool, stats in sorted(report.by_tool.items()):
        acc = stats["correct"] / stats["total"] if stats["total"] else 0
        table.add_row(tool, f"{acc:.1%}", f"{stats['correct']}/{stats['total']}")
    console.print(table)

    if report.mismatches:
        console.print("\n[bold yellow]Mismatches:[/bold yellow]")
        for m in report.mismatches[:5]:
            console.print(f"  [dim]{m['expected']} → [red]{m['actual']}[/red] | {m['query']}")


@eval_app.command("coherence")
def eval_coherence(
    trace_id: str = typer.Option("", "--trace-id", "-t", help="Trace ID to evaluate (omit for all)"),
):
    """Multi-step reasoning coherence evaluation (uses an independent Judge model)."""
    from agentnexus.core.config import get_settings
    from agentnexus.evaluation.coherence import CoherenceEvaluator

    evaluator = CoherenceEvaluator()
    traces_dir = get_settings().traces_dir

    if trace_id:
        report = evaluator.evaluate_trace(trace_id, traces_dir)
        if report is None:
            console.print(f"[red]Trace not found: {trace_id}[/red]")
            return
        reports = [report]
    else:
        reports = evaluator.evaluate_all(traces_dir)

    if not reports:
        console.print("[dim]No evaluation data available[/dim]")
        return

    table = Table(title="Multi-step Coherence Evaluation (Judge: GLM-4.7-Flash)", box=box.ROUNDED)
    table.add_column("Trace ID", style="cyan")
    table.add_column("Steps", justify="right")
    table.add_column("Coherence", justify="right")
    table.add_column("Verdict")

    passed = 0
    for r in reports:
        icon = "[green]PASS[/green]" if r.passed else "[red]FAIL[/red]"
        if r.passed:
            passed += 1
        table.add_row(r.trace_id, str(r.total_steps),
                      f"{r.coherence_score:.1f}", icon)

    console.print(table)
    console.print(f"Passed: {passed}/{len(reports)} (threshold: >8.5/10)")

    for r in reports:
        if not r.passed and r.issues:
            console.print(f"\n[bold red]FAIL {r.trace_id}[/bold red]")
            console.print(f"  [dim]{r.issues[:300]}[/dim]")


