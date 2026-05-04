"""CLI run and version commands"""
import typer
from rich.panel import Panel

from . import app, console


@app.command()
def run(
    task: str = typer.Argument(..., help="要执行的任务描述"),
    non_interactive: bool = typer.Option(False, "--non-interactive", "-n", help="跳过交互确认，自动执行代码"),
):
    """执行一个任务"""
    from agentnexus.agents.multi_agent.orchestrator import orchestrator_persistent
    from agentnexus.core.config import get_settings
    from agentnexus.observability.tracer import trace_manager

    trace_manager.configure(get_settings().traces_dir)
    ctx = trace_manager.start_trace(task)

    console.print(Panel(f"[bold]{task}[/bold]", title="任务"))

    config = {"configurable": {"thread_id": ctx.trace_id}}
    result = orchestrator_persistent.invoke(
        {"task": task, "trace_id": ctx.trace_id}, config=config
    )

    analysis = result.get("analysis", "")
    if analysis:
        console.print(Panel(analysis[:3000], title="结果", border_style="green"))
    console.print(
        f"重试: {result.get('retry_count', 0)}"
    )

    trace_manager.end_trace()


@app.command()
def version():
    """显示版本"""
    console.print("[bold]AgentNexus[/bold] v0.1.0")
