"""CLI run and version commands"""
import typer
from rich.panel import Panel

from . import app, console


@app.command()
def run(task: str = typer.Argument(..., help="要执行的任务描述")):
    """执行一个任务"""
    from agentnexus.agents.multi_agent.orchestrator import orchestrator
    from agentnexus.observability.tracer import trace_manager
    from agentnexus.core.config import get_settings

    trace_manager.configure(get_settings().traces_dir)
    ctx = trace_manager.start_trace(task)

    console.print(Panel(f"[bold]{task}[/bold]", title="任务"))
    result = orchestrator.invoke({"task": task, "trace_id": ctx.trace_id})
    analysis = result.get("analysis", "")
    if analysis:
        console.print(Panel(analysis[:3000], title="结果", border_style="green"))
    console.print(f"评分: {result.get('critique_score', 'N/A')}  重试: {result.get('retry_count', 0)}")

    trace_manager.end_trace()


@app.command()
def version():
    """显示版本"""
    console.print("[bold]AgentNexus[/bold] v0.1.0")
