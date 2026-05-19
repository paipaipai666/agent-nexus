"""CLI run and version commands"""
import uuid

import typer
from rich.panel import Panel

from agentnexus.memory.manager import MemoryManager

from . import app, console


@app.command()
def run(
    task: str = typer.Argument(..., help="要执行的任务描述"),
    non_interactive: bool = typer.Option(False, "--non-interactive", "-n", help="跳过交互确认，自动执行代码"),
    no_memory: bool = typer.Option(False, "--no-memory", help="禁用长期记忆"),
):
    """执行一个任务"""
    from agentnexus.agents.multi_agent.orchestrator import (
        orchestrator_persistent,
        set_orchestrator_memory,
        set_budget_tracker,
    )
    from agentnexus.core.config import get_settings
    from agentnexus.core.budget import BudgetTracker
    from agentnexus.core.llm import AgentLLM
    from agentnexus.observability.tracer import trace_manager

    trace_manager.configure(get_settings().traces_dir)
    ctx = trace_manager.start_trace(task)

    # Cross-session memory
    session_id = f"run_{uuid.uuid4().hex[:12]}"
    memory = MemoryManager(session_id, llm=AgentLLM(), enable_long_term=not no_memory)
    set_orchestrator_memory(memory)

    # Token budget
    budget = BudgetTracker.from_task(task)
    set_budget_tracker(budget)

    console.print(Panel(f"[bold]{task}[/bold]", title="任务"))

    config = {"configurable": {"thread_id": ctx.trace_id}}
    import time as _time
    result = orchestrator_persistent.invoke(
        {"task": task, "trace_id": ctx.trace_id, "memory_session_id": session_id,
         "started_at": _time.time(), "tool_call_count": 0,
         "plan_complexity": "", "plan_metadata": {}},
        config=config,
    )

    analysis = result.get("analysis", "")
    if analysis:
        console.print(Panel(analysis, title="结果", border_style="green"))
    console.print(
        f"重试: {result.get('retry_count', 0)}"
    )

    # Persist learnings from this task
    memory.conclude(task, analysis)

    trace_manager.end_trace()


@app.command()
def version():
    """显示版本"""
    console.print("[bold]AgentNexus[/bold] v0.1.0")
