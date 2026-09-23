"""Memory benchmark CLI: ``nexus eval memory-bench ...``.

End-to-end conversational memory QA (long-term + short-term/state tracking)
against the real memory system (``--backend nexus``) or the full-context
reference (``--backend naive``). Needs LLM access; use ``list`` to inspect
the built-in suite offline.
"""

import json

import typer
from rich import box
from rich.table import Table

from agentnexus.cli import console, eval_app
from agentnexus.cli.eval.benchmark import _write_payload

memory_bench_app = typer.Typer(help="会话记忆基准(LongMemEval/LOCOMO 式 QA 评测,需要 LLM)")
eval_app.add_typer(memory_bench_app, name="memory-bench")


def _load_conversations(dataset: str):
    from agentnexus.eval.memory.dataset import load_builtin_suite, load_suite

    if not dataset or dataset == "sample":
        return load_builtin_suite(), "sample"
    return load_suite(dataset), dataset


def _build_backend(name: str):
    from agentnexus.core.llm import get_default_llm
    from agentnexus.eval.memory.backend import NaiveBackend, NexusBackend

    llm = get_default_llm()
    if name == "naive":
        return NaiveBackend(llm)
    if name == "nexus":
        return NexusBackend(llm)
    raise ValueError(f"unknown backend {name!r}, choose from: nexus, naive")


@memory_bench_app.command("list")
def memory_bench_list():
    """列出内置样例套件的对话与题型分布。"""
    from collections import Counter

    from agentnexus.eval.memory.dataset import load_builtin_suite

    suite = load_builtin_suite()
    type_counts: Counter[str] = Counter()
    for conv in suite:
        for q in conv.questions:
            type_counts[q.qa_type] += 1

    table = Table(title=f"内置记忆基准套件 ({len(suite)} 段对话)", box=box.ROUNDED)
    table.add_column("Conversation", style="cyan")
    table.add_column("Turns", justify="right")
    table.add_column("Questions", justify="right")
    for conv in suite:
        table.add_row(conv.id, str(len(conv.turns)), str(len(conv.questions)))
    console.print(table)

    console.print("题型分布: " + ", ".join(f"{t}={n}" for t, n in sorted(type_counts.items())))
    console.print("[dim]自定义套件: JSONL,每行 {\"id\", \"turns\": [{role, content}], "
                  "\"questions\": [{id, question, answer, qa_type}]}，用 --dataset 传入[/dim]")


@memory_bench_app.command("convert")
def memory_bench_convert(
    source: str = typer.Argument(..., help="公开数据源: locomo | longmemeval-s"),
    output: str = typer.Option(..., "--output", "-o", help="输出套件 JSONL 路径"),
    offline: bool = typer.Option(False, "--offline", help="不联网,只读缓存"),
):
    """下载并转换公开记忆基准(LoCoMo / LongMemEval-S)为套件 JSONL。

    数据缓存在 ~/.cache/agentnexus/benchmarks/memory/,可用环境变量
    AGENTNEXUS_BENCHMARK_CACHE 覆盖。LoCoMo 为 CC BY-NC 4.0,仅限研究/内部评测。
    """
    from collections import Counter

    from agentnexus.eval.memory.convert import load_and_convert, write_suite

    conversations = load_and_convert(source, offline=offline)
    path = write_suite(conversations, output)

    n_q = sum(len(c.questions) for c in conversations)
    type_counts: Counter[str] = Counter(
        q.qa_type for c in conversations for q in c.questions)
    console.print(f"[green]+[/green] {source} → {path}")
    console.print(f"  {len(conversations)} 段对话, {n_q} 题: "
                  + ", ".join(f"{t}={n}" for t, n in sorted(type_counts.items())))


@memory_bench_app.command("re-judge")
def memory_bench_rejudge(
    input_report: str = typer.Option(..., "--input", "-i", help="已生成的评测报告 JSON"),
    output: str = typer.Option("", "--output", "-o", help="输出路径(默认覆盖原报告)"),
    skip_judged: bool = typer.Option(False, "--skip-judged",
                                     help="跳过已有 judge 结论的题(换 judge 重判时不要带此项)"),
):
    """对存盘报告逐题补 judge 结论——生成与判分解耦,judge 可错峰/换模型后补。

    只读取报告里已存的 answer,不重新生成;出错/未答的题跳过。
    """
    import json
    from pathlib import Path

    from agentnexus.core.judge_llm import get_judge_llm
    from agentnexus.eval.memory.scoring import judge_correct

    path = Path(input_report)
    data = json.loads(path.read_text(encoding="utf-8"))
    judge = get_judge_llm()

    n_judged = n_skipped = 0
    with console.status("[bold green]Judging stored answers..."):
        for r in data["results"]:
            if r.get("error") or not r.get("answer"):
                n_skipped += 1
                continue
            if skip_judged and r.get("judge_correct") is not None:
                n_skipped += 1
                continue
            r["judge_correct"] = judge_correct(
                judge, r["question"], r["gold"], r["answer"])
            n_judged += 1

    judged = [r for r in data["results"] if r.get("judge_correct") is not None]
    acc = sum(1 for r in judged if r["judge_correct"]) / len(judged) if judged else None
    data["judge_accuracy"] = round(acc, 4) if acc is not None else None
    data["judge_model"] = getattr(judge, "model", None)  # audit trail for cross-judge runs

    out_path = Path(output) if output else path
    out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    console.print(f"[green]+[/green] judged {n_judged} (skipped {n_skipped}) → {out_path}")
    console.print(f"  judge_accuracy: {data['judge_accuracy']}")


@memory_bench_app.command("run")
def memory_bench_run(
    backend: str = typer.Option("nexus", "--backend", "-b",
                                help="被测后端: nexus(产品记忆系统) 或 naive(全量上下文基线)"),
    dataset: str = typer.Option("sample", "--dataset", "-d",
                                help="套件 JSONL 路径,默认内置 sample"),
    judge: bool = typer.Option(False, "--judge", help="启用独立 judge 模型逐题判分"),
    limit: int = typer.Option(0, "--limit", "-n", help="只跑前 N 段对话(0=全部)"),
    min_f1: float = typer.Option(0.0, "--min-f1", help="CI 门禁: overall F1 低于该值时退出码为 1"),
    report: str = typer.Option("", "--report", "-o", help="报告导出路径(默认 traces/evals/)"),
    ci: bool = typer.Option(False, "--ci", "-c", help="CI 模式: 报告写入 traces/evals/ 并打印 JSON 路径"),
):
    """运行会话记忆基准:回放对话 → 逐题回答 → char-F1(可选 judge)打分。"""
    import datetime

    conversations, dataset_name = _load_conversations(dataset)
    if limit > 0:
        conversations = conversations[:limit]

    backend_obj = _build_backend(backend)
    judge_llm = None
    if judge:
        from agentnexus.core.judge_llm import get_judge_llm
        judge_llm = get_judge_llm()

    from agentnexus.eval.memory.runner import MemoryBenchRunner

    runner = MemoryBenchRunner(backend_obj, judge_llm=judge_llm, dataset_name=dataset_name)
    with console.status(f"[bold green]Running memory bench ({backend})..."):
        result = runner.run(conversations)

    console.print(result.summary())

    table = Table(title="Per-question", box=box.ROUNDED)
    table.add_column("Q", style="dim")
    table.add_column("Type")
    table.add_column("Gold", style="cyan")
    table.add_column("Answer", max_width=60)
    table.add_column("F1", justify="right")
    table.add_column("Ctx", justify="center")
    for r in result.results:
        f1 = f"{r.f1:.2f}" if not r.error else "[red]ERR[/red]"
        hit = {True: "[green]✓[/green]", False: "[red]✗[/red]"}.get(r.retrieval_hit, "—")
        table.add_row(r.question_id, r.qa_type, r.gold, r.answer[:60], f1, hit)
    console.print(table)

    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M%S")
    payload = json.dumps(result.to_dict(), ensure_ascii=False, indent=2)
    _write_payload(payload, report, ci, f"memory-bench-{backend_obj.name}-{stamp}.json")

    if result.error_count:
        console.print(f"[red]{result.error_count} 题执行出错[/red]")
        raise typer.Exit(1)
    if result.overall_f1 < min_f1:
        console.print(f"[red]overall F1 {result.overall_f1:.3f} < --min-f1 {min_f1}[/red]")
        raise typer.Exit(1)
