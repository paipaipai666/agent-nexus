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

EXPORT_FORMATS = {"json", "csv"}


def _rag_evaluator_cls():
    from agentnexus.cli import eval_cmd

    return eval_cmd.get_rag_evaluator_cls()

# ── RAG evaluation ────────────────────────────────────────────────


@eval_app.command("list")
def eval_list():
    """List available evaluation datasets."""
    from agentnexus.rag.eval_dataset import EVAL_SAMPLES, KNOWLEDGE_BASE

    console.print(f"[bold]Knowledge base documents:[/bold] {len(KNOWLEDGE_BASE)}")
    kb_mode = "File-based" if KNOWLEDGE_BASE and all(Path(item).exists() for item in KNOWLEDGE_BASE) else "Inline text"
    console.print(f"[bold]Knowledge base type:[/bold] {kb_mode}")
    console.print(f"[bold]Evaluation samples:[/bold] {len(EVAL_SAMPLES)}\n")

    table = Table(title="Evaluation Samples", box=box.ROUNDED)
    table.add_column("#", style="dim", justify="right")
    table.add_column("Question")
    table.add_column("Ground Truth (excerpt)", style="dim")

    for i, sample in enumerate(EVAL_SAMPLES, 1):
        gt_display = sample.ground_truth
        table.add_row(str(i), sample.question, gt_display)

    console.print(table)


@eval_app.command("run")
def eval_run(
    ci: bool = typer.Option(False, "--ci", "-c", help="CI mode: exit(1) if thresholds not met"),
    top_k: int = typer.Option(10, "--top-k", "-k", help="Retrieval cutoff for Hit Rate / MRR"),
    dataset: str = typer.Option("", "--dataset", "-D", help="External JSONL eval dataset path"),
    output: str = typer.Option("", "--output", "-o", help="Export report path, or '-' for stdout"),
    export_format: str = typer.Option("json", "--format", "-f", help="Export format: json or csv"),
    quick: bool = typer.Option(False, "--quick", "-q", help="Quick mode: run only 4 representative combinations"),
    parallel: bool = typer.Option(False, "--parallel", "-p", help="Parallel mode: multi-threaded evaluation"),
    jobs: int = typer.Option(8, "--jobs", "-j", help="Number of parallel threads (use with --parallel)"),
    verbose: bool = typer.Option(False, "--verbose", "-V", help="Verbose mode: output per-step timing and progress"),
    timeout: int = typer.Option(120, "--timeout", "-T", help="Per-call LLM timeout in seconds (0=unlimited)"),
):
    """Run RAG evaluation and output metrics report."""
    from agentnexus.rag.eval_dataset import DATASET_VERSION, EVAL_SAMPLES, KNOWLEDGE_BASE, load_eval_dataset
    from agentnexus.rag.evaluator import DEFAULT_RAG_THRESHOLDS

    output_console = Console(stderr=True) if output == "-" else console
    if output:
        _validate_export_format(export_format)

    if dataset:
        kb, samples, dataset_version = load_eval_dataset(dataset)
        kb_mode = "File-based" if kb and all(Path(item).exists() for item in kb) else "Inline text"
        output_console.print(
            f"[bold]External dataset loaded:[/bold] {dataset} ({len(samples)} samples, version={dataset_version}, {kb_mode})"
        )
    else:
        kb, samples, dataset_version = KNOWLEDGE_BASE, EVAL_SAMPLES, DATASET_VERSION

    output_console.print("[bold]Running RAG evaluation...[/bold]\n")
    _print_eval_runtime_summary(output_console)

    evaluator = _rag_evaluator_cls()(kb, samples)

    from agentnexus.rag.ingestion import ChunkStrategy

    all_combinations: list[tuple[ChunkStrategy, int, int, bool]] = [
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

    quick_combinations: list[tuple[ChunkStrategy, int, int, bool]] = [
        (ChunkStrategy.FIXED, 512, 64, False),
        (ChunkStrategy.RECURSIVE, 512, 64, False),
        (ChunkStrategy.RECURSIVE, 512, 64, True),
        (ChunkStrategy.SEMANTIC, 512, 64, True),
    ]

    combinations = quick_combinations if quick else all_combinations
    max_workers = jobs if parallel else 1
    if quick:
        output_console.print("[yellow]⚡ Quick mode: running 4 representative combinations[/yellow]")
    if max_workers > 1:
        output_console.print(f"[cyan]⚡ Parallel mode: {max_workers} threads[/cyan]")
    output_console.print()

    results = []
    for strategy, chunk_size, overlap, use_hybrid in combinations:
        label = f"{strategy.value}-{chunk_size}-{'hybrid' if use_hybrid else 'dense'}"
        output_console.print(f"  [{len(results) + 1}/{len(combinations)}] Running: {label}...", end=" ")
        try:
            run = evaluator.run_combination(
                strategy, chunk_size, overlap, use_hybrid,
                top_k=top_k, max_workers=max_workers,
                verbose=verbose, call_timeout=timeout,
            )
            results.append(run)
            output_console.print(f"[green]✓[/green] faithfulness={run.faithfulness:.3f}")
        except Exception as e:
            output_console.print(f"[red]✗ {e}[/red]")

    if not results:
        output_console.print("[red]All evaluation combinations failed[/red]")
        return

    table = Table(title="RAG Evaluation Results", box=box.ROUNDED)
    table.add_column("Config", style="cyan")
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
    output_console.print(f"\n[bold green]Best configuration:[/bold green] {best.label} (faithfulness={best.faithfulness:.3f})")

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
    output_console.print(f"[dim]Report saved: {report_path}[/dim]")
    if output:
        export_path = _export_eval_report(report_data, output, export_format)
        if export_path:
            output_console.print(f"[dim]Exported {export_format.lower()} report: {export_path}[/dim]")

    # CI gate
    if ci:
        output_console.print("\n[bold]CI Gate Check:[/bold]")
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
            output_console.print("\n[bold green]All passed ✓[/bold green]")
        else:
            output_console.print("\n[bold red]Some combinations below threshold:[/bold red]")
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
    """List historical RAG evaluation reports."""

    report_dir = Path(get_settings().traces_dir) / "evals"
    if not report_dir.exists():
        console.print("[dim]No historical evaluation reports[/dim]")
        return

    files = sorted(report_dir.glob("eval_report_*.json"), reverse=True)
    if not files:
        console.print("[dim]No historical evaluation reports[/dim]")
        return

    table = Table(title="Historical Evaluation Reports", box=box.ROUNDED)
    table.add_column("Time", style="cyan")
    table.add_column("Dataset Version")
    table.add_column("Best Config")
    table.add_column("Faithfulness", justify="right")
    table.add_column("HitRate", justify="right")
    table.add_column("MRR", justify="right")
    table.add_column("Configs", justify="right")

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
    baseline: str = typer.Option(..., "--baseline", "-b", help="Baseline report JSON path"),
    candidate: str = typer.Option(..., "--candidate", "-c", help="Candidate report JSON path"),
):
    """Compare two RAG evaluation results."""
    b_path = Path(baseline)
    c_path = Path(candidate)

    if not b_path.exists():
        console.print(f"[red]Baseline file not found: {baseline}[/red]")
        return
    if not c_path.exists():
        console.print(f"[red]Candidate file not found: {candidate}[/red]")
        return

    b_raw = json.loads(b_path.read_text(encoding="utf-8"))
    c_raw = json.loads(c_path.read_text(encoding="utf-8"))

    b_configs = b_raw.get("configs", b_raw if isinstance(b_raw, list) else [])
    c_configs = c_raw.get("configs", c_raw if isinstance(c_raw, list) else [])
    b_version = b_raw.get("dataset_version", "unknown") if isinstance(b_raw, dict) else "unknown"
    c_version = c_raw.get("dataset_version", "unknown") if isinstance(c_raw, dict) else "unknown"

    if b_version != c_version:
        console.print(f"[yellow]⚠ Dataset version mismatch: baseline={b_version} vs candidate={c_version}[/yellow]\n")

    b_map = {r["label"]: r for r in b_configs}
    c_map = {r["label"]: r for r in c_configs}

    metrics = ["faithfulness", "answer_relevancy", "hit_rate", "mrr", "context_precision", "context_recall"]

    table = Table(title=f"Comparison: {baseline} vs {candidate}", box=box.ROUNDED)
    table.add_column("Config", style="cyan")
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


