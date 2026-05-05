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
    table.add_column("Latency(ms)", justify="right")

    for r in sorted(results, key=lambda x: x.faithfulness, reverse=True):
        table.add_row(
            r.label,
            f"{r.faithfulness:.3f}",
            f"{r.answer_relevancy:.3f}",
            f"{r.context_precision:.3f}",
            f"{r.context_recall:.3f}",
            f"{r.avg_latency_ms:.1f}",
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
