"""CLI eval list/run commands"""
import json
from datetime import datetime
from importlib.util import find_spec
from pathlib import Path
from urllib.parse import urlparse

import typer
from rich import box
from rich.table import Table

from agentnexus.core.config import get_settings
from agentnexus.rag.evaluator import RAGEvaluator
from agentnexus.rag.ingestion import ChunkStrategy

from . import console, eval_app

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


def _fmt_ci(score: float, ci: tuple | None = None) -> str:
    if ci and len(ci) == 2:
        return f"{score:.3f} [{ci[0]:.2f}-{ci[1]:.2f}]"
    return f"{score:.3f}"


def _fmt_pct(score: float) -> str:
    return f"{score:.1%}"


def _text_setting(value, default: str = "unknown") -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return default


def _endpoint_mode(base_url: str) -> str:
    parsed = urlparse(base_url)
    host = (parsed.hostname or "").casefold()
    if host in {"localhost", "127.0.0.1", "::1"}:
        return "local"
    if host:
        return "remote"
    return "unknown"


def _detect_embedding_device() -> str:
    try:
        from agentnexus.rag.chroma_client import _resolve_embedding_device

        return _resolve_embedding_device()
    except Exception:
        return "cpu"


def _collect_eval_runtime_summary() -> list[str]:
    settings = get_settings()
    embedding_model = _text_setting(getattr(settings, "embedding_model", None))
    reranker_model = _text_setting(getattr(settings, "reranker_model", None))
    llm_model = _text_setting(getattr(settings, "llm_model_id", None))
    llm_base = _text_setting(getattr(settings, "llm_base_url", None))
    judge_model = _text_setting(getattr(settings, "judge_model_id", None))
    judge_base = _text_setting(getattr(settings, "judge_base_url", None))

    embedding_backend = "fallback-hash"
    if find_spec("sentence_transformers") is not None:
        embedding_backend = "sentence-transformers"

    device = _detect_embedding_device()

    gpu_enabled = embedding_backend == "sentence-transformers" and device in {"cuda", "mps"}
    gpu_label = "yes" if gpu_enabled else "no"

    return [
        f"Embedding: {embedding_model} | backend={embedding_backend} | device={device} | GPU={gpu_label}",
        "Dense retrieval: enabled",
        "Hybrid retrieval: BM25 + dense embeddings",
        f"Reranker: disabled in `nexus eval run` (configured model: {reranker_model})",
        f"Generator LLM: {llm_model} | endpoint={_endpoint_mode(llm_base)} | base={llm_base}",
        f"Judge LLM: {judge_model} | endpoint={_endpoint_mode(judge_base)} | base={judge_base}",
        "Query rewrite / multi-query / HyDE: not used by current `eval run` path",
    ]


def _print_eval_runtime_summary() -> None:
    console.print("[bold]评估运行信息:[/bold]")
    for line in _collect_eval_runtime_summary():
        console.print(f"  - {line}")
    console.print()


@eval_app.command("run")
def eval_run(
    ci: bool = typer.Option(False, "--ci", "-c", help="CI 模式：不达标则 exit(1)"),
    top_k: int = typer.Option(10, "--top-k", "-k", help="检索排序截断数（Hit Rate / MRR 的 k）"),
    dataset: str = typer.Option("", "--dataset", "-d", help="外部 JSONL 评测集路径"),
):
    """运行 RAG 评估并输出指标报告"""
    from agentnexus.rag.eval_dataset import DATASET_VERSION, EVAL_SAMPLES, KNOWLEDGE_BASE, load_eval_dataset
    from agentnexus.rag.evaluator import DEFAULT_RAG_THRESHOLDS

    if dataset:
        kb, samples, dataset_version = load_eval_dataset(dataset)
        kb_mode = "文件型" if kb and all(Path(item).exists() for item in kb) else "内联文本"
        console.print(
            f"[bold]已加载外部数据集:[/bold] {dataset} ({len(samples)} 样本, version={dataset_version}, {kb_mode})"
        )
    else:
        kb, samples, dataset_version = KNOWLEDGE_BASE, EVAL_SAMPLES, DATASET_VERSION

    console.print("[bold]正在运行 RAG 评估...[/bold]\n")
    _print_eval_runtime_summary()

    evaluator = RAGEvaluator(kb, samples)

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
            run = evaluator.run_combination(strategy, chunk_size, overlap, use_hybrid, top_k=top_k)
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

    console.print(table)

    # Highlight best
    best = max(results, key=lambda r: r.faithfulness)
    console.print(f"\n[bold green]最优配置:[/bold green] {best.label} (faithfulness={best.faithfulness:.3f})")

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
    console.print(f"[dim]报告已保存: {report_path}[/dim]")

    # CI gate
    if ci:
        console.print("\n[bold]CI 门禁检查:[/bold]")
        all_passed = True
        for r in results:
            show = []
            if not r.check_passed():
                show.append(f"  [red]✗ {r.label}: FAIL[/red]")
                all_passed = False
            else:
                show.append(f"  [green]✓ {r.label}: PASS[/green]")
            for line in show:
                console.print(line)
        if all_passed:
            console.print("\n[bold green]全部通过 ✓[/bold green]")
        else:
            console.print("\n[bold red]部分组合未达标，阈值:[/bold red]")
            for k, v in sorted(DEFAULT_RAG_THRESHOLDS.items()):
                console.print(f"  {k}: {v}")
            raise typer.Exit(code=1)


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


@eval_app.command("trajectory")
def eval_trajectory(
    trace_id: str = typer.Option("", "--trace-id", "-t", help="Trace ID to evaluate (omit for all)"),
    days: int = typer.Option(7, "--days", "-d", help="Look back N days"),
):
    """运行轨迹质量评估（确定性规则，无 LLM-as-Judge）"""
    from agentnexus.core.config import get_settings
    from agentnexus.evaluation.trajectory import TrajectoryEvaluator

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
    days: int = typer.Option(1, "--days", "-d", help="回溯天数"),
):
    """CI 模式: 单 Agent 质量评估，不达标则 exit(1)"""
    from agentnexus.evaluation.agent_eval import AgentEvaluator

    evaluator = AgentEvaluator()
    report = evaluator.evaluate_all(get_settings().traces_dir, days=days)

    if report.total_traces == 0:
        console.print("[dim]No traces to evaluate[/dim]")
        return

    console.print(report.summary())
    if report.failed_traces:
        console.print(f"\n[red]{len(report.failed_traces)}/{report.total_traces} traces 异常[/red]")
    if not report.passed:
        raise typer.Exit(code=1)


@eval_app.command("component")
def eval_component():
    """运行组件级评估（单 Agent 质量检查：Coder/Researcher/Executor/Analyst）"""
    from agentnexus.core.config import get_settings
    from agentnexus.evaluation.component import ComponentEvaluator

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
    from agentnexus.core.config import get_settings
    from agentnexus.evaluation.hallucination import HallucinationDetector

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
    from agentnexus.core.config import get_settings
    from agentnexus.evaluation.tool_selection import ToolSelectionEvaluator

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
    from agentnexus.core.config import get_settings
    from agentnexus.evaluation.coherence import CoherenceEvaluator

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


@eval_app.command("calibrate")
def eval_calibrate(
    output: str = typer.Option("./calibrate_samples.json", "--output", "-o", help="输出文件路径"),
    score_file: str = typer.Option(
        "",
        "--score-file",
        "-s",
        help="人工评分 JSON 文件路径（含 human_precision/human_recall 字段）",
    ),
):
    """Judge 校准：导出样本供人工打分，计算 Judge 与人工评分的一致性"""
    from agentnexus.rag.chroma_client import delete_collection
    from agentnexus.rag.eval_dataset import EVAL_SAMPLES, KNOWLEDGE_BASE
    from agentnexus.rag.evaluator import RAGEvaluator
    from agentnexus.rag.retriever import HybridRetriever, build_knowledge_base

    evaluator = RAGEvaluator(KNOWLEDGE_BASE, EVAL_SAMPLES)
    strategy, chunk_size, overlap, use_hybrid = ChunkStrategy.FIXED, 256, 64, False

    console.print(f"[bold]校准运行: {strategy.value}-{chunk_size}-dense[/bold]\n")

    samples = []
    chunks = evaluator._chunk_all(strategy, chunk_size, overlap)

    delete_collection(namespace="eval")
    build_knowledge_base(chunks, load_reranker=False, namespace="eval")

    retriever = HybridRetriever(namespace="eval")
    retriever.rebuild_from_catalog()

    for idx, sample in enumerate(EVAL_SAMPLES):
        _, retrieved = evaluator._retrieve(
            sample.question,
            retriever,
            use_hybrid,
            max_tokens=max(len(sample.question) * 5, 100),
        )
        if not retrieved:
            samples.append({
                "sample_idx": idx,
                "question": sample.question,
                "ground_truth": sample.ground_truth,
                "is_negative": not sample.ground_truth,
                "retrieved": [],
                "judge_precision": 0.0,
                "judge_recall": 0.0,
                "judge_faithfulness": 0.0,
                "judge_relevancy": 0.0,
                "human_precision": None,
                "human_recall": None,
            })
            continue

        answer = evaluator._generate_answer(sample.question, retrieved)
        judge_precision = evaluator._score_precision(sample, retrieved)
        judge_recall = evaluator._score_recall(sample, retrieved)
        judge_faithfulness = evaluator._score_faithfulness(answer, retrieved)
        judge_relevancy = (
            evaluator._score_correctness(sample.question, answer, sample.ground_truth)
            if sample.ground_truth
            else 0.0
        )

        samples.append({
            "sample_idx": idx,
            "question": sample.question,
            "ground_truth": sample.ground_truth,
            "is_negative": not sample.ground_truth,
            "retrieved": retrieved[:3],
            "judge_precision": round(judge_precision, 4),
            "judge_recall": round(judge_recall, 4),
            "judge_faithfulness": round(judge_faithfulness, 4),
            "judge_relevancy": round(judge_relevancy, 4),
            "human_precision": None,
            "human_recall": None,
        })

    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(samples, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    if score_file:
        _compute_calibration(samples, score_file)
    else:
        console.print(
            f"[green]已导出 {len(samples)} 个校准样本到: {output_path}[/green]"
        )
        console.print(
            "\n填写每个样本的 [bold]human_precision[/bold] 和 "
            "[bold]human_recall[/bold] (0.0~1.0)，然后重新运行:"
        )
        console.print(f"  [dim]nexus eval calibrate --score-file {output_path}[/dim]")


def _compute_calibration(samples: list[dict], score_file: str) -> None:
    """Compute Spearman/Pearson correlation between Judge and human scores."""
    score_path = Path(score_file)
    if not score_path.exists():
        console.print(f"[red]评分文件不存在: {score_file}[/red]")
        return

    try:
        human_scores = json.loads(score_path.read_text(encoding="utf-8"))
    except Exception as e:
        console.print(f"[red]读取评分文件失败: {e}[/red]")
        return

    human_map = {}
    for h in human_scores:
        if h.get("human_precision") is not None or h.get("human_recall") is not None:
            human_map[h["sample_idx"]] = h

    judge_pre = []
    judge_rec = []
    human_pre = []
    human_rec = []
    labels = []

    for s in samples:
        idx = s["sample_idx"]
        if idx not in human_map:
            continue
        h = human_map[idx]
        jp, jr = s["judge_precision"], s["judge_recall"]
        hp, hr = h.get("human_precision"), h.get("human_recall")
        if jp is not None and hp is not None:
            judge_pre.append(jp)
            human_pre.append(hp)
            labels.append(f"#{idx}")
        if jr is not None and hr is not None:
            judge_rec.append(jr)
            human_rec.append(hr)

    console.print(f"\n[bold]校准结果[/bold] ({len(labels)} 个样本)\n")

    if len(judge_pre) >= 3:
        sp, pp = _spearman(judge_pre, human_pre)
        pr, _ = _pearson(judge_pre, human_pre)
        console.print(f"[bold]Precision[/bold]  Spearman ρ={sp:.3f} (p={pp:.4f})  Pearson r={pr:.3f}")

        console.print("  Judge → Human 散点 (Precision):")
        for j, h, lbl in sorted(zip(judge_pre, human_pre, labels), key=lambda x: x[1]):
            bar = "█" * max(1, int(h * 20))
            console.print(f"    {lbl:>4s}  J={j:.2f}  H={h:.2f}  {bar}")
    else:
        console.print("[yellow]Precision: 样本太少 (<3)，无法计算相关性[/yellow]")

    if len(judge_rec) >= 3:
        sr, prr = _spearman(judge_rec, human_rec)
        rr, _ = _pearson(judge_rec, human_rec)
        console.print(f"[bold]Recall[/bold]     Spearman ρ={sr:.3f} (p={prr:.4f})  Pearson r={rr:.3f}")

        console.print("  Judge → Human 散点 (Recall):")
        for j, h, lbl in sorted(zip(judge_rec, human_rec, labels), key=lambda x: x[1]):
            bar = "█" * max(1, int(h * 20))
            console.print(f"    {lbl:>4s}  J={j:.2f}  H={h:.2f}  {bar}")

    console.print("\n[dim]ρ > 0.7: 强相关 | ρ 0.4~0.7: 中等相关 | ρ < 0.4: 弱相关[/dim]")
    console.print("[dim]Judge 评分一致性达标阈值: Spearman ρ > 0.7[/dim]")


def _spearman(x: list[float], y: list[float]) -> tuple[float, float]:
    """Compute Spearman rank correlation with p-value (manual implementation)."""
    n = len(x)
    if n < 3:
        return (0.0, 1.0)
    try:
        from scipy.stats import spearmanr
        res = spearmanr(x, y)
        return (float(res.statistic), float(res.pvalue))
    except ImportError:
        pass

    # Manual fallback
    def _rank(vals):
        sorted_vals = sorted(vals)
        return [sorted_vals.index(v) + 1 for v in vals]

    rx, ry = _rank(x), _rank(y)
    d2 = sum((a - b) ** 2 for a, b in zip(rx, ry))
    rho = 1.0 - (6.0 * d2) / (n * (n * n - 1))
    # Approximate p-value via t-distribution
    import math
    try:
        t_stat = rho * math.sqrt((n - 2) / (1 - rho * rho))
        from scipy.stats import t as t_dist
        p_val = 2.0 * t_dist.sf(abs(t_stat), df=n - 2)
    except Exception:
        p_val = 0.0
    return (round(rho, 4), round(p_val, 4))


def _pearson(x: list[float], y: list[float]) -> tuple[float, float]:
    """Compute Pearson correlation coefficient (manual implementation)."""
    n = len(x)
    if n < 3:
        return (0.0, 1.0)
    if all(v == x[0] for v in x) or all(v == y[0] for v in y):
        return (0.0, 1.0)
    try:
        from scipy.stats import pearsonr
        res = pearsonr(x, y)
        return (float(res.statistic), float(res.pvalue))
    except ImportError:
        pass

    mx, my = sum(x) / n, sum(y) / n
    num = sum((a - mx) * (b - my) for a, b in zip(x, y))
    den = (sum((a - mx) ** 2 for a in x) * sum((b - my) ** 2 for b in y)) ** 0.5
    if den == 0:
        return (0.0, 1.0)
    r = num / den
    import math
    try:
        t_stat = r * math.sqrt((n - 2) / (1 - r * r))
        from scipy.stats import t as t_dist
        p_val = 2.0 * t_dist.sf(abs(t_stat), df=n - 2)
    except Exception:
        p_val = 0.0
    return (round(r, 4), round(p_val, 4))
