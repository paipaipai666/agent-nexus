"""RAG eval CLI commands."""

import csv
import json
import sys
from datetime import datetime
from pathlib import Path

import typer
from rich import box
from rich.console import Console
from rich.table import Table

from agentnexus.cli import console, eval_app
from agentnexus.cli.eval.common import _fmt_ci, _print_eval_runtime_summary
from agentnexus.core.config import get_settings
from agentnexus.rag.ingestion import ChunkStrategy

EXPORT_FORMATS = {"json", "csv"}


def _rag_evaluator_cls():
    from agentnexus.cli import eval_cmd

    return getattr(eval_cmd, "RAGEvaluator")

# ── RAG evaluation ────────────────────────────────────────────────


@eval_app.command("list")
def eval_list():
    """列出可用的评估数据集"""
    from agentnexus.rag.eval_dataset import EVAL_SAMPLES, KNOWLEDGE_BASE

    console.print(f"[bold]知识库文档:[/bold] {len(KNOWLEDGE_BASE)} 篇")
    kb_mode = "文件型" if KNOWLEDGE_BASE and all(Path(item).exists() for item in KNOWLEDGE_BASE) else "内联文本"
    console.print(f"[bold]知识库类型:[/bold] {kb_mode}")
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
def eval_run(
    ci: bool = typer.Option(False, "--ci", "-c", help="CI 模式：不达标则 exit(1)"),
    top_k: int = typer.Option(10, "--top-k", "-k", help="检索排序截断数（Hit Rate / MRR 的 k）"),
    dataset: str = typer.Option("", "--dataset", "-d", help="外部 JSONL 评测集路径"),
    output: str = typer.Option("", "--output", "-o", help="Export report path, or '-' for stdout"),
    export_format: str = typer.Option("json", "--format", "-f", help="Export format: json or csv"),
):
    """运行 RAG 评估并输出指标报告"""
    from agentnexus.rag.eval_dataset import DATASET_VERSION, EVAL_SAMPLES, KNOWLEDGE_BASE, load_eval_dataset
    from agentnexus.rag.evaluator import DEFAULT_RAG_THRESHOLDS

    output_console = Console(stderr=True) if output == "-" else console
    if output:
        _validate_export_format(export_format)

    if dataset:
        kb, samples, dataset_version = load_eval_dataset(dataset)
        kb_mode = "文件型" if kb and all(Path(item).exists() for item in kb) else "内联文本"
        output_console.print(
            f"[bold]已加载外部数据集:[/bold] {dataset} ({len(samples)} 样本, version={dataset_version}, {kb_mode})"
        )
    else:
        kb, samples, dataset_version = KNOWLEDGE_BASE, EVAL_SAMPLES, DATASET_VERSION

    output_console.print("[bold]正在运行 RAG 评估...[/bold]\n")
    _print_eval_runtime_summary(output_console)

    evaluator = _rag_evaluator_cls()(kb, samples)

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
        output_console.print(f"  [{len(results) + 1}/{len(combinations)}] 运行: {label}...", end=" ")
        try:
            run = evaluator.run_combination(strategy, chunk_size, overlap, use_hybrid, top_k=top_k)
            results.append(run)
            output_console.print(f"[green]✓[/green] faithfulness={run.faithfulness:.3f}")
        except Exception as e:
            output_console.print(f"[red]✗ {e}[/red]")

    if not results:
        output_console.print("[red]所有评估组合均失败[/red]")
        return

    table = Table(title="RAG 评估结果", box=box.ROUNDED)
    table.add_column("配置", style="cyan")
    table.add_column("Faithfulness", justify="right")
    table.add_column("AnsRel", justify="right")
    table.add_column("AnsCorr", justify="right")
    table.add_column("Precision", justify="right")
    table.add_column("Recall", justify="right")
    table.add_column("CtxRel", justify="right")
    table.add_column("HitRate", justify="right")
    table.add_column("MRR", justify="right")
    table.add_column("Reject", justify="right")
    table.add_column("p95(ms)", justify="right")

    for r in sorted(results, key=lambda x: x.faithfulness, reverse=True):
        table.add_row(
            r.label,
            _fmt_ci(r.faithfulness, getattr(r, "faithfulness_ci", None)),
            _fmt_ci(r.answer_relevancy, getattr(r, "answer_relevancy_ci", None)),
            _fmt_ci(r.answer_correctness, getattr(r, "answer_correctness_ci", None)),
            _fmt_ci(r.context_precision, getattr(r, "context_precision_ci", None)),
            _fmt_ci(r.context_recall, getattr(r, "context_recall_ci", None)),
            _fmt_ci(r.context_relevancy, getattr(r, "context_relevancy_ci", None)),
            _fmt_ci(r.hit_rate, getattr(r, "hit_rate_ci", None)),
            _fmt_ci(r.mrr, getattr(r, "mrr_ci", None)),
            f"{getattr(r, 'rejection_rate', 0.0):.1%}",
            f"{r.p95_latency_ms:.0f}",
        )

    output_console.print(table)

    # Highlight best
    best = max(results, key=lambda r: r.faithfulness)
    output_console.print(f"\n[bold green]最优配置:[/bold green] {best.label} (faithfulness={best.faithfulness:.3f})")

    # Save report
    report_dir = Path(get_settings().traces_dir) / "evals"
    report_dir.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = report_dir / f"eval_report_{date_str}.json"
    report_data = {
        "dataset_version": dataset_version,
        "top_k": top_k,
        "configs": [
            {
                "label": r.label,
                "strategy": r.strategy.value,
                "chunk_size": r.chunk_size,
                "use_hybrid": r.use_hybrid,
                "faithfulness": r.faithfulness,
                "answer_relevancy": r.answer_relevancy,
                "answer_correctness": r.answer_correctness,
                "context_precision": r.context_precision,
                "context_recall": r.context_recall,
                "context_relevancy": r.context_relevancy,
                "hit_rate": r.hit_rate,
                "mrr": r.mrr,
                "avg_latency_ms": r.avg_latency_ms,
                "p95_latency_ms": r.p95_latency_ms,
                "rejection_rate": getattr(r, "rejection_rate", 0.0),
            }
            for r in results
        ],
    }
    report_path.write_text(json.dumps(report_data, ensure_ascii=False, indent=2), encoding="utf-8")
    output_console.print(f"[dim]报告已保存: {report_path}[/dim]")
    if output:
        export_path = _export_eval_report(report_data, output, export_format)
        if export_path:
            output_console.print(f"[dim]Exported {export_format.lower()} report: {export_path}[/dim]")

    # CI gate
    if ci:
        output_console.print("\n[bold]CI 门禁检查:[/bold]")
        all_passed = True
        for r in results:
            show = []
            if not r.check_passed():
                show.append(f"  [red]✗ {r.label}: FAIL[/red]")
                all_passed = False
            else:
                show.append(f"  [green]✓ {r.label}: PASS[/green]")
            for line in show:
                output_console.print(line)
        if all_passed:
            output_console.print("\n[bold green]全部通过 ✓[/bold green]")
        else:
            output_console.print("\n[bold red]部分组合未达标，阈值:[/bold red]")
            for k, v in sorted(DEFAULT_RAG_THRESHOLDS.items()):
                output_console.print(f"  {k}: {v}")
            raise typer.Exit(code=1)



def _export_eval_report(report_data: dict, output: str, export_format: str) -> str:
    fmt = _validate_export_format(export_format)
    if output == "-":
        if fmt == "json":
            sys.stdout.write(json.dumps(report_data, ensure_ascii=False, indent=2) + "\n")
        else:
            sys.stdout.write(_report_to_csv(report_data))
        return ""

    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "json":
        output_path.write_text(json.dumps(report_data, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        output_path.write_text(_report_to_csv(report_data), encoding="utf-8", newline="")
    return str(output_path)


def _validate_export_format(export_format: str) -> str:
    fmt = (export_format or "json").strip().lower()
    if fmt not in EXPORT_FORMATS:
        raise typer.BadParameter(f"Unsupported export format: {export_format}. Use json or csv.")
    return fmt


def _report_to_csv(report_data: dict) -> str:
    import io

    columns = [
        "dataset_version",
        "top_k",
        "label",
        "strategy",
        "chunk_size",
        "use_hybrid",
        "faithfulness",
        "answer_relevancy",
        "answer_correctness",
        "context_precision",
        "context_recall",
        "context_relevancy",
        "hit_rate",
        "mrr",
        "avg_latency_ms",
        "p95_latency_ms",
        "rejection_rate",
    ]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    for config in report_data.get("configs", []):
        row = {
            "dataset_version": report_data.get("dataset_version", ""),
            "top_k": report_data.get("top_k", ""),
            **{key: config.get(key, "") for key in columns if key not in {"dataset_version", "top_k"}},
        }
        writer.writerow(row)
    return buf.getvalue()


@eval_app.command("history")
def eval_history():
    """列出历史 RAG 评估报告"""

    report_dir = Path(get_settings().traces_dir) / "evals"
    if not report_dir.exists():
        console.print("[dim]暂无历史评估报告[/dim]")
        return

    files = sorted(report_dir.glob("eval_report_*.json"), reverse=True)
    if not files:
        console.print("[dim]暂无历史评估报告[/dim]")
        return

    table = Table(title="历史评估报告", box=box.ROUNDED)
    table.add_column("时间", style="cyan")
    table.add_column("数据集版本")
    table.add_column("最优配置")
    table.add_column("Faithfulness", justify="right")
    table.add_column("HitRate", justify="right")
    table.add_column("MRR", justify="right")
    table.add_column("配置数", justify="right")

    for file in files[:20]:
        try:
            raw = json.loads(file.read_text(encoding="utf-8"))
            configs = raw.get("configs", raw if isinstance(raw, list) else [])
            version = raw.get("dataset_version", "unknown")
            ts = file.stem.replace("eval_report_", "")
            best = max(configs, key=lambda r: r.get("faithfulness", 0))
            table.add_row(
                ts,
                version,
                best.get("label", "-"),
                f"{best.get('faithfulness', 0):.3f}",
                f"{best.get('hit_rate', 0):.3f}",
                f"{best.get('mrr', 0):.3f}",
                str(len(configs)),
            )
        except Exception:
            continue

    console.print(table)


@eval_app.command("compare")
def eval_compare(
    baseline: str = typer.Option(..., "--baseline", "-b", help="基准报告 JSON 路径"),
    candidate: str = typer.Option(..., "--candidate", "-c", help="候选报告 JSON 路径"),
):
    """对比两次 RAG 评估结果"""
    b_path = Path(baseline)
    c_path = Path(candidate)

    if not b_path.exists():
        console.print(f"[red]基准文件不存在: {baseline}[/red]")
        return
    if not c_path.exists():
        console.print(f"[red]候选文件不存在: {candidate}[/red]")
        return

    b_raw = json.loads(b_path.read_text(encoding="utf-8"))
    c_raw = json.loads(c_path.read_text(encoding="utf-8"))

    b_configs = b_raw.get("configs", b_raw if isinstance(b_raw, list) else [])
    c_configs = c_raw.get("configs", c_raw if isinstance(c_raw, list) else [])
    b_version = b_raw.get("dataset_version", "unknown") if isinstance(b_raw, dict) else "unknown"
    c_version = c_raw.get("dataset_version", "unknown") if isinstance(c_raw, dict) else "unknown"

    if b_version != c_version:
        console.print(f"[yellow]⚠ 数据集版本不一致: baseline={b_version} vs candidate={c_version}[/yellow]\n")

    b_map = {r["label"]: r for r in b_configs}
    c_map = {r["label"]: r for r in c_configs}

    metrics = ["faithfulness", "answer_relevancy", "hit_rate", "mrr", "context_precision", "context_recall"]

    table = Table(title=f"对比: {baseline} vs {candidate}", box=box.ROUNDED)
    table.add_column("配置", style="cyan")
    for m in metrics:
        table.add_column(m, justify="right")

    for label in sorted(set(list(b_map.keys()) + list(c_map.keys()))):
        b = b_map.get(label, {})
        c = c_map.get(label, {})
        row = [label]
        for m in metrics:
            bv = b.get(m, 0)
            cv = c.get(m, 0)
            delta = cv - bv
            if delta > 0.01:
                row.append(f"[green]{cv:.3f} (Δ+{delta:.3f})[/green]")
            elif delta < -0.01:
                row.append(f"[red]{cv:.3f} (Δ{delta:.3f})[/red]")
            else:
                row.append(f"{cv:.3f}")
        table.add_row(*row)

    console.print(table)


