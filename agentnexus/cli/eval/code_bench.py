"""Code benchmark eval CLI commands."""

import typer

from agentnexus.cli import console, eval_app

# ── HumanEval ──────────────────────────────────────────────────────


@eval_app.command("humaneval")
def eval_humaneval(
    dataset: str = typer.Option(
        "", "--dataset", "-D", help="HumanEval JSONL 数据集路径",
    ),
    trace_id: str = typer.Option(
        "", "--trace", "-t", help="按 trace_id 过滤单个问题",
    ),
):
    """评估 HumanEval 代码生成质量（需传入 solutions JSON）"""
    from pathlib import Path

    from agentnexus.evaluation.humaneval import HumanEvalEvaluator

    dataset_path = dataset or str(
        Path(__file__).parents[2] / "tests" / "evals" / "humaneval.jsonl"
    )

    evaluator = HumanEvalEvaluator()
    samples = evaluator.load_dataset(dataset_path)

    if not samples:
        console.print("[yellow]数据集为空或路径错误[/yellow]")
        return

    console.print(f"[dim]已加载 {len(samples)} 个 HumanEval 问题[/dim]")

    if trace_id:
        matched = [s for s in samples if s.trace_id == trace_id]
        if not matched:
            console.print(f"[red]未找到 trace_id: {trace_id}[/red]")
            return
        sample = matched[0]
        console.print(f"\n[bold]问题:[/bold] {sample.question[:200]}")
        console.print(f"[bold]测试用例:[/bold] {len(sample.test_cases)} 个")


@eval_app.command("swe-bench")
def eval_swebench(
    dataset: str = typer.Option(
        "", "--dataset", "-D", help="SWE-bench JSONL 数据集路径",
    ),
):
    """评估 SWE-bench issue 修复质量（需传入 patches JSON）"""
    from pathlib import Path

    from agentnexus.evaluation.swebench import SWEBenchEvaluator

    dataset_path = dataset or str(
        Path(__file__).parents[2] / "tests" / "evals" / "swebench.jsonl"
    )

    evaluator = SWEBenchEvaluator()
    samples = evaluator._inner.load_dataset(dataset_path)

    if not samples:
        console.print("[yellow]数据集为空或路径错误[/yellow]")
        return

    console.print(f"[dim]已加载 {len(samples)} 个 SWE-bench issue[/dim]")
    for s in samples:
        repo = getattr(s, "repo", "?")
        console.print(f"  [{s.trace_id}] {repo} — {len(s.test_cases)} test cases")


