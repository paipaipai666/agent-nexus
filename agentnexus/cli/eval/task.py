"""Eval task CLI — 任务/套件管理与运行。

命令:
  nexus eval task list        -- 列出所有 task
  nexus eval task show <id>   -- 显示 task 详情
  nexus eval task validate    -- 验证数据集
  nexus eval task run         -- 运行 task 或 suite
  nexus eval suite list       -- 列出所有套件
  nexus eval suite run <name> -- 运行套件
  nexus eval suite baseline   -- baseline 管理
"""

from __future__ import annotations

import json
import sys
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from agentnexus.cli import eval_app, console

# 注册 task 和 suite 子命令到 eval_app
task_app = typer.Typer(name="task", help="评估任务管理")
suite_app = typer.Typer(name="suite", help="评估套件管理")
eval_app.add_typer(task_app, name="task")
eval_app.add_typer(suite_app, name="suite")


# ---------------------------------------------------------------------------
# Task Commands
# ---------------------------------------------------------------------------

@task_app.command("list")
def task_list(
    category: Optional[str] = typer.Option(None, "-c", "--category", help="按类别过滤"),
    difficulty: Optional[str] = typer.Option(None, "-d", "--difficulty", help="按难度过滤"),
    eval_type: Optional[str] = typer.Option(None, "-t", "--type", help="按评估类型过滤"),
    json_output: bool = typer.Option(False, "--json", help="JSON 输出"),
) -> None:
    """列出所有评估任务。"""
    from agentnexus.services.eval import EvalService

    service = EvalService()
    tasks = service.list_tasks(category=category, difficulty=difficulty, eval_type=eval_type)

    if json_output:
        console.print_json(json.dumps(tasks, ensure_ascii=False))
        return

    if not tasks:
        console.print("[yellow]没有找到匹配的任务[/yellow]")
        return

    table = Table(title=f"评估任务 ({len(tasks)} 个)")
    table.add_column("ID", style="cyan")
    table.add_column("类别", style="green")
    table.add_column("难度", style="yellow")
    table.add_column("类型", style="magenta")
    table.add_column("描述", max_width=50)
    table.add_column("Graders", justify="right")

    for t in tasks:
        table.add_row(
            t["id"],
            t["category"],
            t["difficulty"],
            t["eval_type"],
            t["description"][:50],
            str(t["grader_count"]),
        )

    console.print(table)


@task_app.command("show")
def task_show(
    task_id: str = typer.Argument(help="任务 ID"),
) -> None:
    """显示任务详情。"""
    from agentnexus.services.eval import EvalService

    service = EvalService()
    task = service.get_task(task_id)
    if task is None:
        console.print(f"[red]任务未找到: {task_id}[/red]")
        raise typer.Exit(1)

    console.print_json(json.dumps(task, ensure_ascii=False, indent=2))


@task_app.command("validate")
def task_validate() -> None:
    """验证数据集完整性。"""
    from agentnexus.services.eval import EvalService

    service = EvalService()
    result = service.validate_dataset()

    if result["valid"]:
        console.print(f"[green]✓ 数据集有效[/green] ({result['stats']['total']} 个任务)")
    else:
        console.print(f"[red]✗ 数据集有 {len(result['errors'])} 个错误[/red]")
        for err in result["errors"]:
            console.print(f"  [red]• {err}[/red]")

    stats = result["stats"]
    if stats.get("by_category"):
        console.print("\n[bold]按类别:[/bold]")
        for cat, count in stats["by_category"].items():
            console.print(f"  {cat}: {count}")

    raise typer.Exit(0 if result["valid"] else 1)


@task_app.command("run")
def task_run(
    task_id: str = typer.Argument(help="任务 ID"),
    trials: int = typer.Option(1, "-n", "--trials", help="Trial 次数"),
    json_output: bool = typer.Option(False, "--json", help="JSON 输出"),
) -> None:
    """运行单个任务。"""
    from agentnexus.services.eval import EvalService

    service = EvalService()

    with console.status(f"[bold green]运行任务 {task_id} ({trials} trials)..."):
        result = service.run_task(task_id, n_trials=trials)

    if json_output:
        console.print_json(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        passed = result.get("passed", False)
        status = "[green]PASSED[/green]" if passed else "[red]FAILED[/red]"
        console.print(Panel(
            f"任务: {result['task_id']}\n"
            f"状态: {status}\n"
            f"Trials: {result.get('n_trials', 0)}\n"
            f"平均分: {result.get('avg_score', 0):.2f}\n"
            f"Pass@1: {result.get('pass_at_k', {}).get('1', 'N/A')}",
            title="任务结果",
        ))


# ---------------------------------------------------------------------------
# Suite Commands
# ---------------------------------------------------------------------------

@suite_app.command("list")
def suite_list(
    json_output: bool = typer.Option(False, "--json", help="JSON 输出"),
) -> None:
    """列出所有评估套件。"""
    from agentnexus.services.eval import EvalService

    service = EvalService()
    suites = service.list_suites()

    if json_output:
        console.print_json(json.dumps(suites, ensure_ascii=False))
        return

    if not suites:
        console.print("[yellow]没有找到套件[/yellow]")
        return

    table = Table(title=f"评估套件 ({len(suites)} 个)")
    table.add_column("名称", style="cyan")
    table.add_column("类型", style="magenta")
    table.add_column("任务数", justify="right")
    table.add_column("描述", max_width=40)

    for s in suites:
        table.add_row(s["name"], s["eval_type"], str(s["task_count"]), s["description"][:40])

    console.print(table)


@suite_app.command("run")
def suite_run(
    suite_name: str = typer.Argument(help="套件名称"),
    trials: int = typer.Option(1, "-n", "--trials", help="每个任务的 Trial 次数"),
    concurrency: int = typer.Option(4, "-j", "--concurrency", help="并发数"),
    ci: bool = typer.Option(False, "--ci", help="CI 模式 (失败时 exit 1)"),
    json_output: bool = typer.Option(False, "--json", help="JSON 输出"),
) -> None:
    """运行评估套件。"""
    from agentnexus.services.eval import EvalService

    service = EvalService()

    with console.status(f"[bold green]运行套件 {suite_name}..."):
        result = service.run_suite(suite_name, n_trials=trials, concurrency=concurrency)

    if json_output:
        console.print_json(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        agg = result.get("aggregate", {})
        passed = result.get("passed", False)
        status = "[green]PASSED[/green]" if passed else "[red]FAILED[/red]"

        console.print(Panel(
            f"套件: {result.get('suite_name', suite_name)} ({result.get('eval_type', '')})\n"
            f"状态: {status}\n"
            f"通过: {agg.get('passed_tasks', 0)}/{agg.get('total_tasks', 0)} ({agg.get('pass_rate', 0):.1%})\n"
            f"平均分: {agg.get('avg_score', 0):.2f}\n"
            f"Trials: {agg.get('total_trials', 0)}\n"
            f"耗时: {agg.get('total_duration_ms', 0):.0f}ms",
            title="套件结果",
        ))

    if ci and not result.get("passed", False):
        console.print("[red]CI gate: 套件未通过[/red]")
        raise typer.Exit(1)


@suite_app.command("show")
def suite_show(
    suite_name: str = typer.Argument(help="套件名称"),
) -> None:
    """显示套件详情。"""
    from agentnexus.services.eval import EvalService

    service = EvalService()
    suite = service.get_suite(suite_name)
    if suite is None:
        console.print(f"[red]套件未找到: {suite_name}[/red]")
        raise typer.Exit(1)

    console.print_json(json.dumps(suite, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------------------
# Baseline Commands
# ---------------------------------------------------------------------------

baseline_app = typer.Typer(name="baseline", help="Baseline 管理")
suite_app.add_typer(baseline_app, name="baseline")


@baseline_app.command("list")
def baseline_list() -> None:
    """列出所有 baseline。"""
    from agentnexus.services.eval import EvalService

    service = EvalService()
    baselines = service.list_baselines()

    if not baselines:
        console.print("[yellow]没有 baseline[/yellow]")
        return

    table = Table(title="Baselines")
    table.add_column("套件", style="cyan")
    table.add_column("时间", style="dim")
    table.add_column("通过率", justify="right")
    table.add_column("平均分", justify="right")

    for b in baselines:
        table.add_row(
            b["suite_name"],
            b["timestamp"][:19],
            f"{b['pass_rate']:.1%}",
            f"{b['avg_score']:.2f}",
        )

    console.print(table)


@baseline_app.command("save")
def baseline_save(
    suite_name: str = typer.Argument(help="套件名称"),
) -> None:
    """保存当前结果为 baseline。"""
    from agentnexus.services.eval import EvalService

    service = EvalService()

    # 运行套件获取当前结果
    console.print(f"[bold]运行套件 {suite_name} 以生成 baseline...[/bold]")
    result = service.run_suite(suite_name)
    path = service.save_baseline(suite_name, result)
    console.print(f"[green]✓ Baseline 已保存到 {path}[/green]")


@baseline_app.command("compare")
def baseline_compare(
    suite_name: str = typer.Argument(help="套件名称"),
) -> None:
    """与 baseline 对比。"""
    from agentnexus.services.eval import EvalService

    service = EvalService()

    console.print(f"[bold]运行套件 {suite_name} 以对比 baseline...[/bold]")
    current = service.run_suite(suite_name)
    regression = service.compare_with_baseline(suite_name, current)

    if "error" in regression:
        console.print(f"[red]{regression['error']}[/red]")
        raise typer.Exit(1)

    has_regression = regression.get("has_regression", False)
    status = "[red]检测到回归[/red]" if has_regression else "[green]无回归[/green]"

    console.print(Panel(
        f"套件: {regression.get('suite_name', suite_name)}\n"
        f"状态: {status}\n"
        f"通过率变化: {regression.get('pass_rate_diff', 0):+.1%}\n"
        f"分数变化: {regression.get('avg_score_diff', 0):+.3f}\n"
        f"回归任务: {', '.join(regression.get('regressions', [])) or '无'}\n"
        f"改进任务: {', '.join(regression.get('improvements', [])) or '无'}",
        title="回归报告",
    ))

    if has_regression:
        raise typer.Exit(1)
