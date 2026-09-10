"""精细化评测 CLI — `eval fg`。

按《AI Agent 应用精细化评测》的范围装配：--scope 选择评测范围，
--judge 启用 LLM 裁判（质量类指标必需）。报告数据优先。
"""

import typer
from rich import box
from rich.table import Table

from agentnexus.cli import console, eval_app


@eval_app.command("fg")
def eval_fine_grained(
    scope: str = typer.Option("full", "--scope", "-s",
                              help="评测范围: perception | planning | tools | e2e | memory | core_module | full"),
    judge: bool = typer.Option(False, "--judge", help="启用 LLM judge（质量类指标必需）"),
    json_out: str = typer.Option("", "--json", help="把完整结果写到指定 JSON 文件"),
):
    """精细化评测：按模块拆解的质量 × 成本 × 性能指标（数据优先，非 PASS/FAIL）。"""
    from agentnexus.evaluation.fine_grained.runner import FineGrainedRunner

    judge_llm = None
    if judge:
        from agentnexus.core.judge_llm import get_judge_llm
        judge_llm = get_judge_llm()

    runner = FineGrainedRunner(judge_llm=judge_llm)
    report = runner.run(scope)
    console.print(report.summary())

    # 用例明细表：verdict + 关键数值
    table = Table(title="用例明细", box=box.ROUNDED)
    table.add_column("用例", style="cyan")
    table.add_column("数据集")
    table.add_column("结果")
    table.add_column("关键数值/理由", overflow="fold")
    for o in report.outcomes:
        verdict_color = {"pass": "green", "fail": "red", "skip": "yellow", "error": "magenta"}.get(
            o.verdict, "white")
        kv = "  ".join(f"{k}={v}" for k, v in o.metric_values.items()
                       if isinstance(v, (int, float))) or ""
        table.add_row(o.case_id, o.dataset, f"[{verdict_color}]{o.verdict}[/{verdict_color}]",
                      f"{kv}  {o.reasoning[:80]}")
    console.print(table)

    if json_out:
        import json
        from pathlib import Path
        Path(json_out).write_text(
            json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        console.print(f"[dim]完整结果已写入 {json_out}[/dim]")
