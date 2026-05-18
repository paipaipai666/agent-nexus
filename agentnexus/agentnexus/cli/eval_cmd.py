"""CLI eval list/run commands"""
import json
from datetime import datetime
from pathlib import Path

import typer
from rich import box
from rich.table import Table

from agentnexus.rag.ingestion import ChunkStrategy
from agentnexus.core.config import get_settings

from . import eval_app, console


@eval_app.command("list")
def eval_list():
    """列出可用的评估数据集"""
    from agentnexus.rag.eval_dataset import KNOWLEDGE_BASE, EVAL_SAMPLES

    console.print(f"[bold]知识库文档:[/bold] {len(KNOWLEDGE_BASE)} 篇")
    console.print(f"[bold]评估样本:[/bold] {len(EVAL_SAMPLES)} 个\n")

    table = Table(title="评估样本列表", box=box.ROUNDED)
    table.add_column("#", style="dim", justify="right")
    table.add_column("问题")
    table.add_column("标准答案（节选）", style="dim")

    for i, sample in enumerate(EVAL_SAMPLES, 1):
        gt_display = sample.ground_truth
        table.add_row(str(i), sample.question, gt_display)

    console.print(table)


@eval_app.command("run")
def eval_run():
    """运行 RAG 评估并输出指标报告"""
    from agentnexus.rag.evaluator import RAGEvaluator
    from agentnexus.rag.eval_dataset import KNOWLEDGE_BASE, EVAL_SAMPLES

    console.print("[bold]正在运行 RAG 评估...[/bold]\n")

    evaluator = RAGEvaluator(KNOWLEDGE_BASE, EVAL_SAMPLES)

    combinations: list[tuple[ChunkStrategy, int, int, bool]] = [
        (ChunkStrategy.FIXED, 256, 64, False),
        (ChunkStrategy.FIXED, 512, 64, False),
        (ChunkStrategy.RECURSIVE, 256, 64, False),
        (ChunkStrategy.RECURSIVE, 512, 64, False),
        (ChunkStrategy.SEMANTIC, 256, 64, False),
        (ChunkStrategy.SEMANTIC, 512, 64, False),
        (ChunkStrategy.FIXED, 256, 64, True),
        (ChunkStrategy.FIXED, 512, 64, True),
        (ChunkStrategy.RECURSIVE, 256, 64, True),
        (ChunkStrategy.RECURSIVE, 512, 64, True),
        (ChunkStrategy.SEMANTIC, 256, 64, True),
        (ChunkStrategy.SEMANTIC, 512, 64, True),
    ]

    results = []
    for strategy, chunk_size, overlap, use_hybrid in combinations:
        label = f"{strategy.value}-{chunk_size}-{'hybrid' if use_hybrid else 'dense'}"
        console.print(f"  [{len(results) + 1}/{len(combinations)}] 运行: {label}...", end=" ")
        try:
            run = evaluator.run_combination(strategy, chunk_size, overlap, use_hybrid)
            results.append(run)
            console.print(f"[green]✓[/green] faithfulness={run.faithfulness:.3f}")
        except Exception as e:
            console.print(f"[red]✗ {e}[/red]")

    if not results:
        console.print("[red]所有评估组合均失败[/red]")
        return

    table = Table(title="RAG 评估结果", box=box.ROUNDED)
    table.add_column("配置", style="cyan")
    table.add_column("Faithfulness", justify="right")
    table.add_column("Relevancy", justify="right")
    table.add_column("Precision", justify="right")
    table.add_column("Recall", justify="right")
    table.add_column("Relevancy", justify="right")
    table.add_column("p95(ms)", justify="right")

    for r in sorted(results, key=lambda x: x.faithfulness, reverse=True):
        table.add_row(
            r.label,
            f"{r.faithfulness:.3f}",
            f"{r.answer_relevancy:.3f}",
            f"{r.context_precision:.3f}",
            f"{r.context_recall:.3f}",
            f"{r.context_relevancy:.3f}",
            f"{r.p95_latency_ms:.0f}",
        )

    console.print(table)

    # Highlight best
    best = max(results, key=lambda r: r.faithfulness)
    console.print(f"\n[bold green]最优配置:[/bold green] {best.label} (faithfulness={best.faithfulness:.3f})")

    # Save report
    report_dir = Path(get_settings().traces_dir) / "evals"
    report_dir.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = report_dir / f"eval_report_{date_str}.json"
    report_data = [
        {
            "label": r.label,
            "strategy": r.strategy.value,
            "chunk_size": r.chunk_size,
            "use_hybrid": r.use_hybrid,
            "faithfulness": r.faithfulness,
            "answer_relevancy": r.answer_relevancy,
            "context_precision": r.context_precision,
            "context_recall": r.context_recall,
            "avg_latency_ms": r.avg_latency_ms,
        }
        for r in results
    ]
    report_path.write_text(json.dumps(report_data, ensure_ascii=False, indent=2), encoding="utf-8")
    console.print(f"[dim]报告已保存: {report_path}[/dim]")


@eval_app.command("trajectory")
def eval_trajectory(
    trace_id: str = typer.Option("", "--trace-id", "-t", help="Trace ID to evaluate (omit for all)"),
    days: int = typer.Option(7, "--days", "-d", help="Look back N days"),
):
    """运行轨迹质量评估（确定性规则，无 LLM-as-Judge）"""
    from agentnexus.evaluation.trajectory import TrajectoryEvaluator
    from agentnexus.core.config import get_settings

    traces_dir = get_settings().traces_dir
    evaluator = TrajectoryEvaluator()

    if trace_id:
        report = evaluator.evaluate_trace(trace_id, traces_dir)
        if report is None:
            console.print(f"[red]未找到 Trace: {trace_id}[/red]")
            return
        reports = [report]
    else:
        reports = evaluator.evaluate_all(traces_dir)

    if not reports:
        console.print("[dim]暂无 trace 数据可评估[/dim]")
        return

    table = Table(title="轨迹评估结果", box=box.ROUNDED)
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
    console.print(f"通过: {passed}/{len(reports)}")

    # Show details for failed traces
    for r in reports:
        if not r.passed:
            console.print(f"\n[bold red]FAIL[/bold red] {r.trace_id} (score={r.score:.1f})")
            for issue in r.issues:
                console.print(f"  [dim]{issue.check}: {issue.detail}[/dim]")


@eval_app.command("ci")
def eval_ci(
    days: int = typer.Option(1, "--days", "-d", help="Check traces from last N days"),
):
    """CI 模式: 对所有 trace 跑轨迹评估，不达标则 exit(1)"""
    from agentnexus.evaluation.trajectory import TrajectoryEvaluator
    from agentnexus.core.config import get_settings

    evaluator = TrajectoryEvaluator()
    reports = evaluator.evaluate_all(get_settings().traces_dir)

    if not reports:
        console.print("[dim]No traces to evaluate[/dim]")
        return

    failed = [r for r in reports if not r.passed]
    for r in failed:
        for issue in r.issues:
            console.print(f"[red]FAIL[/red] {r.trace_id}: {issue.check} — {issue.detail}")

    console.print(f"\n[bold]{'[green]All passed' if not failed else f'[red]{len(failed)}/{len(reports)} failed'}")
    if failed:
        raise typer.Exit(code=1)


@eval_app.command("component")
def eval_component():
    """运行组件级评估（单 Agent 质量检查：Coder/Researcher/Executor/Analyst）"""
    from agentnexus.evaluation.component import ComponentEvaluator
    from agentnexus.core.config import get_settings

    evaluator = ComponentEvaluator()
    report = evaluator.evaluate_all(get_settings().traces_dir)

    if report.total_traces == 0:
        console.print("[dim]暂无 trace 数据可评估[/dim]")
        return

    table = Table(title="组件评估结果", box=box.ROUNDED)
    table.add_column("Agent", style="cyan")
    table.add_column("评分", justify="right")
    table.add_column("检查次数", justify="right")

    for agent, info in sorted(report.by_agent.items()):
        table.add_row(agent, f"{info['score']:.1f}", str(info["count"]))

    console.print(table)
    console.print(f"总 Trace: {report.total_traces} | 问题: {report.issue_count}")

    if report.by_tool:
        console.print()
        tool_table = Table(title="工具执行成功率", box=box.ROUNDED)
        tool_table.add_column("工具", style="cyan")
        tool_table.add_column("成功率", justify="right")
        tool_table.add_column("成功/总数")
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
    """幻觉率检测：提取答案中的声明，验证是否在上下文中"""
    from agentnexus.evaluation.hallucination import HallucinationDetector
    from agentnexus.core.config import get_settings

    detector = HallucinationDetector()
    traces_dir = get_settings().traces_dir

    if trace_id:
        report = detector.evaluate_trace(trace_id, traces_dir)
        if report is None:
            console.print(f"[red]未找到 Trace: {trace_id}[/red]")
            return
        reports = [report]
    else:
        reports = detector.evaluate_all(traces_dir)

    if not reports:
        console.print("[dim]暂无评估数据[/dim]")
        return

    table = Table(title="幻觉率检测", box=box.ROUNDED)
    table.add_column("Trace ID", style="cyan")
    table.add_column("声明数", justify="right")
    table.add_column("无依据", justify="right")
    table.add_column("幻觉率", justify="right")
    table.add_column("判定")

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
    console.print(f"\n整体幻觉率: {overall:.1%} | 总声明: {total_claims} | 无依据: {total_unsupported}")

    for r in reports:
        if not r.passed and r.flagged_claims:
            console.print(f"\n[bold red]FAIL {r.trace_id}[/bold red]")
            for c in r.flagged_claims[:3]:
                console.print(f"  [dim]⚠ {c}[/dim]")


@eval_app.command("tool-selection")
def eval_tool_selection():
    """工具选择准确率：对比 Agent 实际选择的工具与预期工具"""
    from agentnexus.evaluation.tool_selection import ToolSelectionEvaluator
    from agentnexus.core.config import get_settings

    evaluator = ToolSelectionEvaluator()
    report = evaluator.evaluate_from_traces(get_settings().traces_dir)

    if report.total_queries == 0:
        console.print("[dim]暂无评估数据[/dim]")
        return

    icon = "[green]PASS[/green]" if report.passed else "[red]FAIL[/red]"
    console.print(f"[bold]工具选择准确率:[/bold] {report.accuracy:.1%} {icon}")
    console.print(f"正确: {report.correct}/{report.total_queries}")
    console.print()

    table = Table(title="按工具分解", box=box.ROUNDED)
    table.add_column("工具", style="cyan")
    table.add_column("准确率", justify="right")
    table.add_column("正确/总数")
    for tool, stats in sorted(report.by_tool.items()):
        acc = stats["correct"] / stats["total"] if stats["total"] else 0
        table.add_row(tool, f"{acc:.1%}", f"{stats['correct']}/{stats['total']}")
    console.print(table)

    if report.mismatches:
        console.print("\n[bold yellow]不匹配:[/bold yellow]")
        for m in report.mismatches[:5]:
            console.print(f"  [dim]{m['expected']} → [red]{m['actual']}[/red] | {m['query']}")


@eval_app.command("coherence")
def eval_coherence(
    trace_id: str = typer.Option("", "--trace-id", "-t", help="Trace ID to evaluate (omit for all)"),
):
    """多步推理连贯性评估（使用独立的 Judge 模型，不同模型家族）"""
    from agentnexus.evaluation.coherence import CoherenceEvaluator
    from agentnexus.core.config import get_settings

    evaluator = CoherenceEvaluator()
    traces_dir = get_settings().traces_dir

    if trace_id:
        report = evaluator.evaluate_trace(trace_id, traces_dir)
        if report is None:
            console.print(f"[red]未找到 Trace: {trace_id}[/red]")
            return
        reports = [report]
    else:
        reports = evaluator.evaluate_all(traces_dir)

    if not reports:
        console.print("[dim]暂无评估数据[/dim]")
        return

    table = Table(title="多步连贯性评估（Judge: GLM-4.7-Flash）", box=box.ROUNDED)
    table.add_column("Trace ID", style="cyan")
    table.add_column("步骤数", justify="right")
    table.add_column("连贯性", justify="right")
    table.add_column("判定")

    passed = 0
    for r in reports:
        icon = "[green]PASS[/green]" if r.passed else "[red]FAIL[/red]"
        if r.passed:
            passed += 1
        table.add_row(r.trace_id, str(r.total_steps),
                      f"{r.coherence_score:.1f}", icon)

    console.print(table)
    console.print(f"通过: {passed}/{len(reports)} (阈值: >8.5/10)")

    for r in reports:
        if not r.passed and r.issues:
            console.print(f"\n[bold red]FAIL {r.trace_id}[/bold red]")
            console.print(f"  [dim]{r.issues[:300]}[/dim]")
