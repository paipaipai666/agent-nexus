"""Benchmark evaluation CLI: ``nexus eval benchmark ...``"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import typer
from rich import box
from rich.console import Console
from rich.table import Table

from agentnexus.cli import eval_app
from agentnexus.eval.benchmarks.schema import SuiteReport

benchmark_app = typer.Typer(help="公开基准评测(BEIR/MTEB 风格,文档级检索,无 LLM 调用)")
eval_app.add_typer(benchmark_app, name="benchmark")

console = Console()


@benchmark_app.command("list")
def benchmark_list():
    """列出可用的基准套件及其数据集。"""
    from agentnexus.eval.benchmarks import list_suites

    for suite in list_suites():
        console.print(f"[bold cyan]{suite.name}[/bold cyan] — {suite.description}")
        for ref in suite.datasets:
            console.print(f"  • {ref.display} (loader={ref.loader})")
        for note in suite.notes:
            console.print(f"  [dim]{note}[/dim]")
        console.print()


@benchmark_app.command("run")
def benchmark_run(
    suite: str = typer.Option("beir-lite", "--suite", "-s", help="基准套件名"),
    datasets: str = typer.Option("", "--datasets", "-d", help="只跑指定数据集,逗号分隔(默认全部)"),
    mode: str = typer.Option("dense", "--mode", "-m", help="检索模式:dense(官方口径)或 hybrid(RRF)"),
    depth: int = typer.Option(100, "--depth", help="每查询检索深度(recall@100 需要 >=100)"),
    k: int = typer.Option(10, "--k", help="NDCG/MAP/Precision/Hits 的截断值"),
    embedding_model: str = typer.Option("", "--embedding-model", "-e", help="临时切换 embedding 模型(如 BAAI/bge-small-en-v1.5)"),
    offline: bool = typer.Option(False, "--offline", help="只使用本地缓存的数据,不下载"),
    ci: bool = typer.Option(False, "--ci", "-c", help="CI 模式:结果写入报告目录并打印 JSON 路径"),
    report: str = typer.Option("", "--report", "-o", help="报告导出路径(默认 traces/evals/)"),
):
    """运行公开基准检索评测(纯本地计算,零 LLM API 调用)。"""
    from agentnexus.eval.benchmarks import get_suite, load_suite_datasets, run_retrieval
    from agentnexus.eval.benchmarks.schema import RETRIEVAL_MODES

    if mode not in RETRIEVAL_MODES:
        console.print(f"[red]Unknown mode '{mode}', choose from {RETRIEVAL_MODES}[/red]")
        raise typer.Exit(code=2)

    try:
        suite_spec = get_suite(suite)
    except KeyError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from exc

    only = tuple(item.strip() for item in datasets.split(",") if item.strip())
    try:
        loaded = load_suite_datasets(suite_spec, only=only, offline=offline)
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    settings = _swap_embedding_model(embedding_model)
    console.print(
        f"[bold]Suite:[/bold] {suite_spec.name}  [bold]Mode:[/bold] {mode}  "
        f"[bold]Embedding:[/bold] {settings.embedding_model}  [bold]Depth:[/bold] {depth}"
    )

    report_obj = SuiteReport(suite=suite_spec.name, run_at=datetime.now(timezone.utc).isoformat())
    exit_code = 0
    for item in loaded:
        data = item.data
        console.print(f"\n[bold]{data.dataset}[/bold] — {len(data.docs)} docs, {len(data.queries)} queries")

        def _progress(done: int, total: int, label=data.dataset):
            console.print(f"  {label}: {done}/{total} queries", end="\r")

        try:
            result = run_retrieval(data, mode=mode, depth=depth, k=k, progress=_progress)
        except Exception as exc:
            console.print(f"[red]  {data.dataset} FAILED: {exc}[/red]")
            exit_code = 1
            continue
        report_obj.results.append(result)
        _print_result(result, k)

    if not report_obj.results:
        console.print("[red]No dataset completed[/red]")
        raise typer.Exit(code=1)

    report_path = _write_report(report_obj, report, ci)
    if report_path:
        console.print(f"\n[green]Report:[/green] {report_path}")

    if ci and exit_code == 0:
        console.print("[dim]CI mode: report written; wire threshold gates per-dataset as needed.[/dim]")
    raise typer.Exit(code=exit_code)


def _print_result(result, result_k: int) -> None:
    table = Table(title=f"{result.dataset} ({result.mode})", box=box.ROUNDED)
    table.add_column("Metric", style="cyan")
    table.add_column("Value", justify="right")
    printed: set[str] = set()
    for name in (f"ndcg@{result_k}", "map", f"hits@{result_k}", f"precision@{result_k}", "recall@100"):
        if name in result.metrics:
            table.add_row(name, f"{result.metrics[name]:.4f}")
            printed.add(name)
    for name, value in result.metrics.items():
        if name not in printed:
            table.add_row(name, f"{value:.4f}")
    table.add_row("queries(with rels)", f"{result.n_queries} ({result.n_queries_with_rels})")
    table.add_row("elapsed", f"{result.elapsed_s:.1f}s")
    console.print(table)


def _write_report(report_obj: SuiteReport, report_opt: str, ci: bool) -> Path | None:
    payload = json.dumps(report_obj.to_dict(), ensure_ascii=False, indent=2)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return _write_payload(payload, report_opt, ci, f"benchmark-{report_obj.suite}-{stamp}.json")


def _write_payload(out_text: str, report_opt: str, ci: bool, default_name: str) -> Path | None:
    if report_opt:
        path = Path(report_opt)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(out_text, encoding="utf-8")
        return path
    if ci:
        from agentnexus.core.config import get_settings

        report_dir = Path(get_settings().traces_dir) / "evals"
        report_dir.mkdir(parents=True, exist_ok=True)
        path = report_dir / default_name
        path.write_text(out_text, encoding="utf-8")
        return path
    return None


@benchmark_app.command("run-rgb")
def benchmark_run_rgb(
    language: str = typer.Option("zh", "--lang", "-l", help="语言: zh 或 en"),
    task: str = typer.Option("noise", "--task", "-t", help="任务: noise(噪声鲁棒) 或 rejection(负样本拒答)"),
    model: str = typer.Option("", "--model", "-m", help="生成模型 selector,如 agnes/agnes-3.0-flash(默认当前 active)"),
    limit: int = typer.Option(50, "--limit", "-n", help="评测题数(0=全部)"),
    passage_num: int = typer.Option(5, "--passage-num", help="每题文档数(RGB 官方=5)"),
    top_k: int = typer.Option(5, "--top-k", "-k", help="检索进入生成的 top-k"),
    mode: str = typer.Option("dense", "--mode", help="检索模式:dense 或 hybrid(生产 RRF 栈)"),
    reinforce_refusal: bool = typer.Option(False, "--reinforce-refusal", help="prompt 追加拒答纪律条款(拒答 A/B 实验)"),
    rpm: float = typer.Option(18.0, "--rpm", help="LLM 调用限速(共享池, Agnes 免费档建议 ≤20)"),
    offline: bool = typer.Option(False, "--offline", help="只使用本地缓存数据"),
    judge_only: str = typer.Option("", "--judge-only", "-j", help="跳过生成,重判指定报告里的存盘答案(错峰/换 judge 对拍)"),
    generate_only: bool = typer.Option(False, "--generate-only", "-g", help="只生成存盘,judge 留待 -j 重判(如 deepseek 低谷时段再判)"),
    embedding_model: str = typer.Option("", "--embedding-model", "-e", help="临时切换 embedding(英文任务建议 BAAI/bge-small-en-v1.5)"),
    ci: bool = typer.Option(False, "--ci", "-c", help="结果写入 traces/evals/ 报告"),
    report: str = typer.Option("", "--report", "-o", help="报告导出路径"),
):
    """运行 RGB 端到端评测(检索+生成+judge,远程 LLM 按限速计费/免费档)。"""
    import json as _json
    from datetime import datetime, timezone

    from agentnexus.eval.benchmarks.e2e import rejudge_report, run_rgb_e2e
    from agentnexus.eval.benchmarks.rgb_loader import load_rgb_queries

    if task not in ("noise", "rejection"):
        console.print(f"[red]Unknown task '{task}', choose from: noise, rejection[/red]")
        raise typer.Exit(code=2)
    if language not in ("zh", "en"):
        console.print(f"[red]Unknown language '{language}', choose from: zh, en[/red]")
        raise typer.Exit(code=2)
    if judge_only and generate_only:
        console.print("[red]--judge-only and --generate-only are mutually exclusive[/red]")
        raise typer.Exit(code=2)

    if judge_only:
        console.print(f"[bold]Re-judging[/bold] {judge_only} (rpm={rpm})")

        def _judge_progress(done: int, total: int):
            console.print(f"  {done}/{total} rejudge", end="\r")

        try:
            result = rejudge_report(judge_only, rpm=rpm, progress=_judge_progress)
        except (ValueError, FileNotFoundError) as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=1) from exc

        console.print()
        table = Table(title=f"Re-judge: {result['original_judge']} -> {result['new_judge']}", box=box.ROUNDED)
        table.add_column("Metric", style="cyan")
        table.add_column("Value", justify="right")
        table.add_row("queries", str(result["n_total"]))
        table.add_row("errors", str(result["n_errors"]))
        table.add_row("accuracy (new)", f"{result['accuracy_new']:.4f}")
        for bucket, acc in sorted(result.get("by_noise_bucket_new", {}).items()):
            table.add_row(bucket, f"{acc:.4f}")
        if result["agreement"] is not None:
            table.add_row("agreement with original", f"{result['agreement']:.4f}")
        console.print(table)

        out_text = _json.dumps(result, ensure_ascii=False, indent=2)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        default_name = f"rgb-rejudge-{stamp}.json"
        # rejudge results are only useful persisted — default to writing the
        # report even without an explicit --ci (stdout alone is lossy).
        _write_payload(out_text, report, True if not report else ci, default_name)
        raise typer.Exit(code=0)

    console.print(f"[bold]RGB {language}/{task}[/bold] — model={model or '(active)'} top_k={top_k} rpm={rpm}"
                  + (" [yellow]generate-only[/yellow]" if generate_only else ""))
    try:
        queries = load_rgb_queries(language=language, task=task, offline=offline)
    except (FileNotFoundError, RuntimeError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    def _progress(done: int, total: int):
        console.print(f"  {done}/{total} queries", end="\r")

    settings = None
    original_embedding = None
    if embedding_model:
        from agentnexus.core.config import get_settings as _get_settings
        from agentnexus.rag.embeddings import reset_embedding_model as _reset_emb

        settings = _get_settings()
        original_embedding = settings.embedding_model
        if embedding_model != original_embedding:
            settings.embedding_model = embedding_model
            _reset_emb()
            console.print(f"[yellow]Embedding temporarily switched:[/yellow] {original_embedding} -> {embedding_model}")

    try:
        summary = run_rgb_e2e(
            queries, model_selector=model, passage_num=passage_num,
            top_k=top_k, limit=limit, rpm=rpm, judge_enabled=not generate_only,
            retrieval_mode=mode, reinforce_refusal=reinforce_refusal, progress=_progress,
        )
    except Exception as exc:
        console.print(f"[red]run failed: {exc}[/red]")
        raise typer.Exit(code=1) from exc
    finally:
        if original_embedding and settings is not None:
            from agentnexus.rag.embeddings import reset_embedding_model as _reset_emb

            settings.embedding_model = original_embedding
            _reset_emb()

    console.print()
    table = Table(title=f"RGB {language}/{task}", box=box.ROUNDED)
    table.add_column("Metric", style="cyan")
    table.add_column("Value", justify="right")
    table.add_row("queries", str(summary.n_total))
    table.add_row("correct", str(summary.n_correct))
    table.add_row("errors", str(summary.n_errors))
    table.add_row("accuracy", f"{summary.accuracy:.4f}" + ("  (judging deferred → run-rgb -j)" if generate_only else ""))
    for bucket, acc in sorted(summary.by_noise_bucket.items()):
        table.add_row(bucket, f"{acc:.4f}")
    table.add_row("avg latency", f"{summary.avg_latency_s:.1f}s")
    console.print(table)

    payload = {
        "kind": "rgb-e2e",
        "run_at": datetime.now(timezone.utc).isoformat(),
        "summary": summary.to_dict(),
        "records": [
            {
                "query_id": r.query_id,
                "noise_rate": round(r.noise_rate, 3),
                "query": r.query,
                "generation": r.generation,
                "correct": r.correct,
                "judge_score": r.judge_score,
                "latency_s": round(r.latency_s, 2),
                "error": r.error,
                "retrieval_pos_hits": r.retrieval_pos_hits,
                "retrieval_pos_total": r.retrieval_pos_total,
            }
            for r in summary.records
        ],
    }
    out_text = _json.dumps(payload, ensure_ascii=False, indent=2)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    _write_payload(out_text, report, ci, f"rgb-{language}-{task}-{stamp}.json")
    raise typer.Exit(code=0)


@benchmark_app.command("gate")
def benchmark_gate(
    suite: str = typer.Option("multihop", "--suite", "-s", help="基准套件名"),
    metric: str = typer.Option("ndcg@10", "--metric", "-m", help="门禁指标名"),
    min_value: float = typer.Option(..., "--min", help="指标下限(含),低于则 exit 1"),
    report: str = typer.Option("", "--report", "-r", help="显式报告路径(默认取该套件最新 benchmark 报告)"),
):
    """CI 回归门禁:报告指标 < 下限则 exit 1。"""
    import json as _json

    path = Path(report) if report else _latest_suite_report(suite)
    if path is None:
        console.print(f"[red]No benchmark report found for suite '{suite}'[/red]")
        raise typer.Exit(code=2)

    payload = _json.loads(path.read_text(encoding="utf-8"))
    failures = []
    for result in payload.get("results", []):
        value = (result.get("metrics") or {}).get(metric)
        dataset = result.get("dataset", "?")
        if value is None:
            failures.append((dataset, None))
            console.print(f"  {dataset}: [red]{metric} missing[/red]")
            continue
        status = "green" if value >= min_value else "red"
        console.print(f"  {dataset}: {metric}={value:.4f} (min {min_value:.4f}) [{status}]")
        if value < min_value:
            failures.append((dataset, value))

    if failures:
        console.print(f"[red]GATE FAILED[/red]: {len(failures)} dataset(s) below {metric} >= {min_value}")
        raise typer.Exit(code=1)
    console.print(f"[green]GATE PASSED[/green]: {path.name}")
    raise typer.Exit(code=0)


@benchmark_app.command("run-multihop")
def benchmark_run_multihop(
    model: str = typer.Option("agnes/agnes-3.0-flash", "--model", "-m", help="生成模型 selector"),
    limit: int = typer.Option(0, "--limit", "-n", help="评测题数(0=全部 2556)"),
    top_k: int = typer.Option(5, "--top-k", "-k", help="检索进入生成的 top-k"),
    rpm: float = typer.Option(18.0, "--rpm", help="LLM 调用限速(Agnes 免费档建议 ≤20)"),
    generate_only: bool = typer.Option(False, "--generate-only", "-g", help="只生成存盘,judge 留待后续"),
    judge_only: str = typer.Option("", "--judge-only", "-j", help="跳过生成,判指定报告里的存盘答案(deepseek 低谷时段用)"),
    embedding_model: str = typer.Option("BAAI/bge-small-en-v1.5", "--embedding-model", "-e", help="embedding 模型"),
    offline: bool = typer.Option(False, "--offline", help="只使用本地缓存数据"),
    ci: bool = typer.Option(False, "--ci", "-c", help="报告写入 traces/evals/"),
    report: str = typer.Option("", "--report", "-o", help="报告导出路径"),
):
    """MultiHop-RAG 端到端(检索+生成+judge)。null_query 规则判拒答(零 judge 成本)。"""
    import json as _json
    from datetime import datetime, timezone

    from agentnexus.eval.benchmarks.multihop_loader import load_multihop

    if judge_only:
        from agentnexus.eval.benchmarks.e2e import MULTIHOP_REFUSAL_PHRASES, judge_noise_answer
        from agentnexus.eval.benchmarks.ratelimit import RateLimiter
        from agentnexus.eval.benchmarks.rgb_loader import RGBQuery

        payload = _json.loads(Path(judge_only).read_text(encoding="utf-8"))
        if payload.get("kind") != "multihop-e2e":
            console.print(f"[red]{judge_only} is not a multihop-e2e report[/red]")
            raise typer.Exit(code=2)

        data = load_multihop(offline=True)
        from agentnexus.core.judge_llm import get_judge_llm

        judge = get_judge_llm()
        limiter = RateLimiter(rpm)
        records = payload["records"]
        console.print(f"[bold]Judging[/bold] {judge_only}: {len(records)} records (rpm={rpm})")

        def _judge_record(record):
            if record.get("error"):
                return record
            gen = record.get("generation") or ""
            if record.get("is_null"):
                lowered = gen.strip().lower()
                record["correct"] = any(p in lowered for p in MULTIHOP_REFUSAL_PHRASES)
                record["judge_score"] = None
                return record
            q = RGBQuery(
                query_id=str(record["query_id"]), query=record["query"],
                answer_groups=[[record["answer"]]] if record.get("answer") else [],
                positive=[], negative=[], task="noise", language="en",
            )
            correct, score, jerr = judge_noise_answer(q, gen, judge, limiter)
            record["correct"], record["judge_score"] = correct, score
            if jerr:
                record["error"] = jerr
            return record

        from concurrent.futures import ThreadPoolExecutor

        workers = max(1, min(6, round(rpm / 10)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            records = list(pool.map(_judge_record, records))

        pos = [r for r in records if not r.get("is_null")]
        nulls = [r for r in records if r.get("is_null")]
        n_err = sum(1 for r in records if r.get("error"))
        acc_pos = sum(1 for r in pos if r.get("correct")) / max(len(pos), 1)
        acc_null = sum(1 for r in nulls if r.get("correct")) / max(len(nulls), 1)
        console.print(f"positive accuracy: {acc_pos:.4f} ({sum(1 for r in pos if r.get('correct'))}/{len(pos)})")
        console.print(f"null refusal accuracy: {acc_null:.4f} ({sum(1 for r in nulls if r.get('correct'))}/{len(nulls)})")
        console.print(f"errors: {n_err}")

        payload["records"] = records
        payload["summary"].update({
            "accuracy_pos": round(acc_pos, 4), "accuracy_null": round(acc_null, 4),
            "accuracy_all": round((sum(1 for r in pos if r.get('correct')) + sum(1 for r in nulls if r.get('correct'))) / max(len(records) - n_err, 1), 4),
            "n_errors": n_err, "judge_model": f"{getattr(judge, 'model', 'judge')}",
        })
        out_text = _json.dumps(payload, ensure_ascii=False, indent=2)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        _write_payload(out_text, report, True if not report else ci, f"multihop-e2e-judged-{stamp}.json")
        raise typer.Exit(code=0)

    from agentnexus.eval.benchmarks.e2e import run_multihop_e2e

    console.print(f"[bold]MultiHop-RAG e2e[/bold] — model={model} top_k={top_k} rpm={rpm}"
                  + (" [yellow]generate-only[/yellow]" if generate_only else ""))
    data = load_multihop(offline=offline)

    settings = None
    original_embedding = None
    if embedding_model:
        from agentnexus.core.config import get_settings as _get_settings
        from agentnexus.rag.embeddings import reset_embedding_model as _reset_emb

        settings = _get_settings()
        original_embedding = settings.embedding_model
        if embedding_model != original_embedding:
            settings.embedding_model = embedding_model
            _reset_emb()

    def _progress(done: int, total: int):
        console.print(f"  {done}/{total} queries", end="\r")

    try:
        summary = run_multihop_e2e(
            data, answers=data.answers, is_null={qid: qid in data.null_query_ids for qid in data.answers},
            model_selector=model, top_k=top_k, limit=limit, rpm=rpm,
            judge_enabled=not generate_only, progress=_progress,
        )
    finally:
        if original_embedding and settings is not None:
            from agentnexus.rag.embeddings import reset_embedding_model as _reset_emb

            settings.embedding_model = original_embedding
            _reset_emb()

    console.print()
    table = Table(title="MultiHop-RAG e2e", box=box.ROUNDED)
    table.add_column("Metric", style="cyan")
    table.add_column("Value", justify="right")
    table.add_row("queries (pos/null)", f"{summary.n_total} ({summary.n_pos}/{summary.n_null})")
    table.add_row("errors", str(summary.n_errors))
    table.add_row("accuracy (positive)", f"{summary.accuracy_pos:.4f}")
    table.add_row("accuracy (null/refusal)", f"{summary.accuracy_null:.4f}")
    table.add_row("accuracy (all)", f"{summary.accuracy_all:.4f}")
    console.print(table)

    payload = {
        "kind": "multihop-e2e",
        "run_at": datetime.now(timezone.utc).isoformat(),
        "summary": {**summary.to_dict(), "model": summary.model, "judge_model": summary.judge_model},
        "records": [
            {
                "query_id": r.query_id, "query": r.query, "answer": r.answer, "is_null": r.is_null,
                "generation": r.generation, "correct": r.correct, "judge_score": r.judge_score,
                "latency_s": round(r.latency_s, 2), "error": r.error,
            }
            for r in summary.records
        ],
    }
    out_text = _json.dumps(payload, ensure_ascii=False, indent=2)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    _write_payload(out_text, report, ci, f"multihop-e2e-{stamp}.json")
    raise typer.Exit(code=0)


def _latest_suite_report(suite: str) -> Path | None:
    from agentnexus.core.config import get_settings

    report_dir = Path(get_settings().traces_dir) / "evals"
    if not report_dir.exists():
        return None
    candidates = sorted(
        report_dir.glob(f"benchmark-{suite}-*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    # exclude the hand-captured baseline file
    candidates = [p for p in candidates if "-baseline-" not in p.name]
    return candidates[0] if candidates else None


def _swap_embedding_model(embedding_model: str):
    """Temporarily switch settings.embedding_model for this run.

    The embedding service caches by model name; we reset it around the swap so
    the benchmark indexes with the requested encoder and the rest of the app
    keeps its configured model afterwards.
    """
    from agentnexus.core.config import get_settings
    from agentnexus.rag.embeddings import reset_embedding_model

    settings = get_settings()
    original = settings.embedding_model
    if embedding_model and embedding_model != original:
        settings.embedding_model = embedding_model
        reset_embedding_model()
        console.print(f"[yellow]Embedding temporarily switched:[/yellow] {original} -> {embedding_model}")
    return settings
