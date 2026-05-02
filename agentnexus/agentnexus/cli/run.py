"""CLI run and version commands

Run command features:
- Multi-agent orchestration with LangGraph state machine
- Task persistence via SQLite checkpointing (supports resume after interruption)
- Human-in-the-loop (HITL): pauses before code execution for user confirmation
"""
import typer
from rich.panel import Panel

from . import app, console


@app.command()
def run(task: str = typer.Argument(..., help="要执行的任务描述")):
    """执行一个任务（支持任务持久化和代码执行确认）

    使用 LangGraph SQLiteCheckpointer 保存状态，支持中断后恢复。
    在执行代码前会暂停并请求用户确认（HITL）。
    """
    from agentnexus.agents.multi_agent.orchestrator import orchestrator_persistent
    from agentnexus.observability.tracer import trace_manager
    from agentnexus.core.config import get_settings

    trace_manager.configure(get_settings().traces_dir)
    ctx = trace_manager.start_trace(task)

    console.print(Panel(f"[bold]{task}[/bold]", title="任务"))

    # Pass thread_id for LangGraph checkpointing (enables resume/recovery)
    config = {"configurable": {"thread_id": ctx.trace_id}}
    result = orchestrator_persistent.invoke(
        {"task": task, "trace_id": ctx.trace_id}, config=config
    )

    # Human-in-the-loop: prompt before high-risk operations (code execution)
    cancelled = False
    while True:
        state = orchestrator_persistent.get_state(config)
        if state and state.next and state.next != ():
            console.print(
                f"\n[yellow]即将执行代码，是否继续？[/yellow] [dim](y/n)[/dim]"
            )
            try:
                response = input().strip().lower()
            except (EOFError, OSError):
                response = 'y'  # Non-interactive: auto-approve
            if response != 'y':
                console.print("[dim]任务已取消[/dim]")
                cancelled = True
                break
            result = orchestrator_persistent.invoke(None, config=config)
        else:
            break

    if not cancelled:
        analysis = result.get("analysis", "")
        if analysis:
            console.print(
                Panel(analysis[:3000], title="结果", border_style="green")
            )
        console.print(
            f"评分: {result.get('critique_score', 'N/A')}  "
            f"重试: {result.get('retry_count', 0)}"
        )

    trace_manager.end_trace()


@app.command()
def version():
    """显示版本"""
    console.print("[bold]AgentNexus[/bold] v0.1.0")
