"""Transcript CLI — 查看和分析 agent 执行 transcript。

对应 Anthropic 方法论: "Read the transcripts!"

命令:
  nexus eval transcript show <trace_id>              -- 显示完整 transcript
  nexus eval transcript show <trace_id> --grader      -- 显示 grader 评分详情
  nexus eval transcript list                           -- 列出最近的 transcripts
  nexus eval transcript search --tool <name>           -- 按工具搜索
  nexus eval transcript failures                       -- 列出失败的 transcripts
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from agentnexus.cli import eval_app, console

app = typer.Typer(name="transcript", help="Transcript 查看和分析")
eval_app.add_typer(app, name="transcript")


def _get_traces_dir() -> Path:
    from agentnexus.core.config import get_settings
    settings = get_settings()
    return Path(settings.traces_dir)


def _load_spans_by_trace(traces_dir: Path, trace_id: str | None = None) -> dict[str, list[dict]]:
    """加载 spans，按 trace_id 分组。"""
    traces: dict[str, list[dict]] = {}
    for f in sorted(traces_dir.glob("*.jsonl"), reverse=True):
        with open(f, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    span = json.loads(line)
                except json.JSONDecodeError:
                    continue
                tid = span.get("trace_id", "unknown")
                if trace_id and tid != trace_id:
                    continue
                traces.setdefault(tid, []).append(span)
    return traces


def _format_span(span: dict, index: int) -> str:
    """格式化单个 span 为可读文本。"""
    name = span.get("name", "unknown")
    start = span.get("start_time", 0)
    end = span.get("end_time", 0)
    latency = span.get("latency_ms", (end - start) * 1000 if end and start else 0)
    meta = span.get("metadata", {}) or {}
    status = meta.get("status", "ok")

    lines = [f"[bold]#{index} [{name}] status={status} latency={latency:.0f}ms[/bold]"]

    # Input
    inp = span.get("input", {}) or {}
    if name == "tool":
        tool_name = inp.get("tool_name", "")
        params = inp.get("params", {})
        lines.append(f"  Tool: {tool_name}")
        if params:
            lines.append(f"  Params: {json.dumps(params, ensure_ascii=False)[:200]}")
    elif name == "llm":
        model = meta.get("model", "")
        in_tok = meta.get("input_tokens", 0)
        out_tok = meta.get("output_tokens", 0)
        lines.append(f"  Model: {model} | tokens: {in_tok} in / {out_tok} out")
    elif name == "task":
        task_text = inp.get("task", "")
        lines.append(f"  Task: {task_text[:200]}")

    # Output
    out = span.get("output", {}) or {}
    if name == "final_answer":
        answer = out.get("answer", "")
        lines.append(f"  Answer: {answer[:300]}")
    elif name == "tool":
        result = out.get("result_summary", "")
        if result:
            lines.append(f"  Result: {str(result)[:200]}")
    elif name == "llm":
        content = out.get("content", "")
        if content:
            lines.append(f"  Content: {str(content)[:200]}")

    # Errors
    if status == "error":
        error = out.get("error", "") or meta.get("error", "")
        if error:
            lines.append(f"  [red]Error: {error[:200]}[/red]")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

@app.command("show")
def transcript_show(
    trace_id: str = typer.Argument(help="Trace ID"),
    grader: bool = typer.Option(False, "--grader", "-g", help="显示 grader 评分详情"),
    json_output: bool = typer.Option(False, "--json", help="JSON 输出"),
) -> None:
    """显示单个 transcript 的完整执行记录。"""
    traces_dir = _get_traces_dir()
    traces = _load_spans_by_trace(traces_dir, trace_id)

    if not traces:
        console.print(f"[red]Trace not found: {trace_id}[/red]")
        raise typer.Exit(1)

    spans = traces[trace_id]
    spans.sort(key=lambda s: s.get("start_time", 0))

    if json_output:
        console.print_json(json.dumps(spans, ensure_ascii=False, indent=2))
        return

    console.print(f"\n[bold]Transcript: {trace_id}[/bold] ({len(spans)} spans)\n")

    for i, span in enumerate(spans):
        name = span.get("name", "unknown")
        color = {
            "task": "blue", "llm": "cyan", "tool": "green",
            "final_answer": "yellow", "plan_node": "dim",
        }.get(name, "white")
        console.print(_format_span(span, i), style=f"color({color})")
        console.print()

    # Summary
    tool_spans = [s for s in spans if s.get("name") == "tool"]
    llm_spans = [s for s in spans if s.get("name") == "llm"]
    error_count = sum(1 for s in spans if s.get("metadata", {}).get("status") == "error")
    total_tokens = sum(
        s.get("metadata", {}).get("input_tokens", 0) +
        s.get("metadata", {}).get("output_tokens", 0)
        for s in llm_spans
    )

    console.print(Panel(
        f"LLM calls: {len(llm_spans)} | Tool calls: {len(tool_spans)} | "
        f"Errors: {error_count} | Total tokens: {total_tokens}",
        title="Summary",
    ))

    if grader:
        _show_grader_scores(spans)


def _show_grader_scores(spans: list[dict]) -> None:
    """对 transcript 运行所有 grader 并显示结果。"""
    from agentnexus.evaluation.graders import (
        TrajectoryGraderAdapter, HallucinationGraderAdapter,
        TranscriptGrader, GraderConfig,
    )

    console.print("\n[bold]Grader Scores:[/bold]\n")

    graders = [
        ("trajectory", TrajectoryGraderAdapter(), GraderConfig(type="trajectory")),
        ("hallucination", HallucinationGraderAdapter(), GraderConfig(type="hallucination")),
        ("transcript", TranscriptGrader(), GraderConfig(type="transcript", max_turns=15)),
    ]

    table = Table()
    table.add_column("Grader", style="cyan")
    table.add_column("Score", justify="right")
    table.add_column("Passed", justify="center")
    table.add_column("Details", max_width=60)

    for name, grader, config in graders:
        try:
            score = grader.grade({}, spans, {}, config)
            passed_str = "[green]✓[/green]" if score.passed else "[red]✗[/red]"
            table.add_row(name, f"{score.score:.2f}", passed_str, score.details[:60])
        except Exception as e:
            table.add_row(name, "ERR", "[red]?[/red]", str(e)[:60])

    console.print(table)


@app.command("list")
def transcript_list(
    days: int = typer.Option(1, "-d", "--days", help="查看最近几天"),
    limit: int = typer.Option(20, "-n", "--limit", help="最多显示几条"),
    tool: Optional[str] = typer.Option(None, "--tool", "-t", help="按工具名过滤"),
    json_output: bool = typer.Option(False, "--json", help="JSON 输出"),
) -> None:
    """列出最近的 transcripts。"""
    import time

    traces_dir = _get_traces_dir()
    cutoff = time.time() - days * 86400

    traces: dict[str, dict] = {}
    for f in sorted(traces_dir.glob("*.jsonl"), reverse=True):
        with open(f, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    span = json.loads(line)
                except json.JSONDecodeError:
                    continue
                tid = span.get("trace_id", "")
                ts = span.get("start_time", 0)
                if ts < cutoff:
                    continue
                name = span.get("name", "")
                if tid not in traces:
                    traces[tid] = {
                        "trace_id": tid,
                        "start_time": ts,
                        "spans": 0,
                        "tools": set(),
                        "has_error": False,
                        "has_answer": False,
                    }
                traces[tid]["spans"] += 1
                if name == "tool":
                    tool_name = (span.get("input", {}) or {}).get("tool_name", "")
                    if tool_name:
                        traces[tid]["tools"].add(tool_name)
                if span.get("metadata", {}).get("status") == "error":
                    traces[tid]["has_error"] = True
                if name == "final_answer":
                    traces[tid]["has_answer"] = True

    # 过滤
    entries = list(traces.values())
    if tool:
        entries = [e for e in entries if tool in e["tools"]]
    entries.sort(key=lambda e: e["start_time"], reverse=True)
    entries = entries[:limit]

    if json_output:
        for e in entries:
            e["tools"] = list(e["tools"])
        console.print_json(json.dumps(entries, ensure_ascii=False, indent=2))
        return

    table = Table(title=f"Recent Transcripts ({len(entries)})")
    table.add_column("Trace ID", style="cyan", max_width=20)
    table.add_column("Spans", justify="right")
    table.add_column("Tools", max_width=30)
    table.add_column("Status", justify="center")

    for e in entries:
        tools_str = ", ".join(sorted(e["tools"]))[:30]
        if e["has_error"]:
            status = "[red]ERROR[/red]"
        elif e["has_answer"]:
            status = "[green]OK[/green]"
        else:
            status = "[yellow]NO ANSWER[/yellow]"
        table.add_row(e["trace_id"][:20], str(e["spans"]), tools_str, status)

    console.print(table)


@app.command("search")
def transcript_search(
    tool: Optional[str] = typer.Option(None, "--tool", "-t", help="按工具名搜索"),
    keyword: Optional[str] = typer.Option(None, "--keyword", "-k", help="按关键字搜索"),
    days: int = typer.Option(7, "-d", "--days", help="搜索最近几天"),
    limit: int = typer.Option(10, "-n", "--limit", help="最多显示几条"),
) -> None:
    """搜索 transcripts。"""
    import time

    traces_dir = _get_traces_dir()
    cutoff = time.time() - days * 86400
    results: list[dict] = []

    for f in sorted(traces_dir.glob("*.jsonl"), reverse=True):
        with open(f, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    span = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if span.get("start_time", 0) < cutoff:
                    continue

                matched = False
                if tool:
                    name = span.get("name", "")
                    if name == "tool":
                        tname = (span.get("input", {}) or {}).get("tool_name", "")
                        if tname == tool:
                            matched = True
                if keyword:
                    text = json.dumps(span, ensure_ascii=False).lower()
                    if keyword.lower() in text:
                        matched = True

                if matched:
                    results.append({
                        "trace_id": span.get("trace_id", ""),
                        "span_name": span.get("name", ""),
                        "start_time": span.get("start_time", 0),
                    })

                if len(results) >= limit:
                    break
            if len(results) >= limit:
                break

    if not results:
        console.print("[yellow]No matching transcripts found[/yellow]")
        return

    table = Table(title=f"Search Results ({len(results)})")
    table.add_column("Trace ID", style="cyan")
    table.add_column("Span", style="green")

    for r in results:
        table.add_row(r["trace_id"], r["span_name"])

    console.print(table)


@app.command("failures")
def transcript_failures(
    days: int = typer.Option(1, "-d", "--days", help="查看最近几天"),
    limit: int = typer.Option(10, "-n", "--limit", help="最多显示几条"),
) -> None:
    """列出失败的 transcripts (无 final_answer 或有 error)。"""
    import time

    traces_dir = _get_traces_dir()
    cutoff = time.time() - days * 86400

    traces: dict[str, dict] = {}
    for f in sorted(traces_dir.glob("*.jsonl"), reverse=True):
        with open(f, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    span = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if span.get("start_time", 0) < cutoff:
                    continue
                tid = span.get("trace_id", "")
                name = span.get("name", "")
                if tid not in traces:
                    traces[tid] = {"trace_id": tid, "has_answer": False, "errors": [], "spans": 0}
                traces[tid]["spans"] += 1
                if name == "final_answer":
                    traces[tid]["has_answer"] = True
                if span.get("metadata", {}).get("status") == "error":
                    err = (span.get("output", {}) or {}).get("error", "unknown")
                    traces[tid]["errors"].append(str(err)[:100])

    # 筛选失败的
    failures = [
        t for t in traces.values()
        if not t["has_answer"] or t["errors"]
    ]
    failures.sort(key=lambda t: t["trace_id"], reverse=True)
    failures = failures[:limit]

    if not failures:
        console.print("[green]No failed transcripts found[/green]")
        return

    table = Table(title=f"Failed Transcripts ({len(failures)})")
    table.add_column("Trace ID", style="red")
    table.add_column("Spans", justify="right")
    table.add_column("Errors", max_width=50)

    for f in failures:
        err_str = "; ".join(f["errors"][:2]) if f["errors"] else "No answer"
        table.add_row(f["trace_id"], str(f["spans"]), err_str[:50])

    console.print(table)
