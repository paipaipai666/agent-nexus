"""Code benchmark eval CLI commands."""

import typer

from agentnexus.cli import console, eval_app

# ── HumanEval ──────────────────────────────────────────────────────


@eval_app.command("humaneval")
def eval_humaneval(
    dataset: str = typer.Option(
        "", "--dataset", "-D", help="HumanEval JSONL dataset path",
    ),
    trace_id: str = typer.Option(
        "", "--trace", "-t", help="Filter a single problem by trace_id",
    ),
):
    """Evaluate HumanEval code generation quality (requires solutions JSON)."""
    from pathlib import Path

    from agentnexus.evaluation.humaneval import HumanEvalEvaluator

    dataset_path = dataset or str(
        Path(__file__).parents[2] / "tests" / "evals" / "humaneval.jsonl"
    )

    evaluator = HumanEvalEvaluator()
    samples = evaluator.load_dataset(dataset_path)

    if not samples:
        console.print("[yellow]Dataset empty or path invalid[/yellow]")
        return

    console.print(f"[dim]Loaded {len(samples)} HumanEval problems[/dim]")

    if trace_id:
        matched = [s for s in samples if s.trace_id == trace_id]
        if not matched:
            console.print(f"[red]trace_id not found: {trace_id}[/red]")
            return
        sample = matched[0]
        console.print(f"\n[bold]Problem:[/bold] {sample.question[:200]}")
        console.print(f"[bold]Test cases:[/bold] {len(sample.test_cases)}")


@eval_app.command("swe-bench")
def eval_swebench(
    dataset: str = typer.Option(
        "", "--dataset", "-D", help="SWE-bench JSONL dataset path",
    ),
):
    """Evaluate SWE-bench issue fixing quality (requires patches JSON)."""
    from pathlib import Path

    from agentnexus.evaluation.swebench import SWEBenchEvaluator

    dataset_path = dataset or str(
        Path(__file__).parents[2] / "tests" / "evals" / "swebench.jsonl"
    )

    evaluator = SWEBenchEvaluator()
    samples = evaluator._inner.load_dataset(dataset_path)

    if not samples:
        console.print("[yellow]Dataset empty or path invalid[/yellow]")
        return

    console.print(f"[dim]Loaded {len(samples)} SWE-bench issues[/dim]")
    for s in samples:
        repo = getattr(s, "repo", "?")
        console.print(f"  [{s.trace_id}] {repo} — {len(s.test_cases)} test cases")


