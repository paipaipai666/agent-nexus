"""CLI run and version commands"""
import sys
import typer
from rich.panel import Panel

from . import app, console


@app.command()
def run(
    task: str = typer.Argument(..., help="要执行的任务描述"),
    non_interactive: bool = typer.Option(False, "--non-interactive", "-n", help="跳过交互确认，自动执行代码"),
):
    """执行一个任务（支持任务持久化和代码执行确认）"""
    from agentnexus.agents.multi_agent.orchestrator import (
        MAX_RETRIES,
        PASS_THRESHOLD,
        _retry_mgr,
        orchestrator_persistent,
    )
    from agentnexus.core.config import get_settings
    from agentnexus.observability.tracer import trace_manager

    _retry_mgr._error_history.clear()

    trace_manager.configure(get_settings().traces_dir)
    ctx = trace_manager.start_trace(task)

    console.print(Panel(f"[bold]{task}[/bold]", title="任务"))

    config = {"configurable": {"thread_id": ctx.trace_id}}
    result = orchestrator_persistent.invoke(
        {"task": task, "trace_id": ctx.trace_id}, config=config
    )

    cancelled = False
    hitl_iterations = 0
    max_hitl = 50
    is_interactive = sys.stdin.isatty() and not non_interactive

    while hitl_iterations < max_hitl:
        state = orchestrator_persistent.get_state(config)
        has_next = state and state.next and state.next != ()

        if not has_next:
            break

        if not is_interactive:
            console.print("  [dim][HITL] 非交互模式，自动继续...[/dim]")
        else:
            console.print("\n[yellow]即将执行代码，是否继续？[/yellow] [dim](y/n)[/dim]")
            try:
                response = input().strip().lower()
            except (EOFError, OSError):
                response = 'y'
            if response != 'y':
                console.print("[dim]任务已取消[/dim]")
                cancelled = True
                break

        result = orchestrator_persistent.invoke(None, config=config)
        hitl_iterations += 1

        if _task_is_done(result):
            break

    if not cancelled:
        analysis = result.get("analysis", "")
        if analysis:
            console.print(Panel(analysis[:3000], title="结果", border_style="green"))
        console.print(
            f"评分: {result.get('critique_score', 'N/A')}  "
            f"重试: {result.get('retry_count', 0)}"
        )

    trace_manager.end_trace()


def _task_is_done(result: dict) -> bool:
    """检查任务是否完成，复用 orchestrator 中的阈值常量。"""
    from agentnexus.agents.multi_agent.orchestrator import MAX_RETRIES, PASS_THRESHOLD

    score = result.get("critique_score", 0)
    retry_count = result.get("retry_count", 0)
    return score >= PASS_THRESHOLD or retry_count >= MAX_RETRIES


@app.command()
def version():
    """显示版本"""
    console.print("[bold]AgentNexus[/bold] v0.1.0")
