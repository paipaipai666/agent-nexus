"""CLI run and version commands"""
import uuid
import warnings

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
    warnings.filterwarnings("ignore", message=".*pkg_resources.*")

    from agentnexus.agents.re_act_agent import ReActAgent
    from agentnexus.cli.audit import _global_audit_log
    from agentnexus.core.config import get_settings
    from agentnexus.core.llm import AgentLLM
    from agentnexus.observability.tracer import trace_manager
    from agentnexus.tools import register_all_tools
    from agentnexus.tools.tool_executor import ToolExecutor

    trace_manager.configure(get_settings().traces_dir)
    ctx = trace_manager.start_trace(task)

    # Cross-session memory
    session_id = f"run_{uuid.uuid4().hex[:12]}"
    raw_llm = AgentLLM()

    def _silent_think(messages, temperature=0):
        return raw_llm.think(messages, temperature, silent=True)

    class _SilentLLM:
        think = staticmethod(_silent_think)

    llm = _SilentLLM()
    memory = MemoryManager(session_id, llm=llm, enable_long_term=not no_memory)

    # Setup tools
    executor = ToolExecutor()
    executor.registry._audit_log = _global_audit_log
    register_all_tools(executor, non_interactive=non_interactive)

    console.print(Panel(f"[bold]{task}[/bold]", title="任务"))

    from rich.markup import escape as _escape

    def _show_step(msg: str):
        if msg.startswith("--- 第"):
            console.print(Panel(msg.strip("- "), border_style="dim", padding=(0, 2)))
        elif msg.startswith("思考:"):
            console.print(Panel(_escape(msg), border_style="blue", padding=(0, 2)))
        elif msg.startswith("行动:"):
            console.print(Panel(_escape(msg), border_style="yellow", padding=(0, 2)))
        elif msg.startswith("观察:"):
            console.print(Panel(_escape(msg), border_style="green", padding=(0, 2)))
        elif msg.startswith(("错误:", "警告:")):
            console.print(f"  [red]{msg}[/red]")
        elif "达到最大步数" in msg:
            console.print(f"  [dim]{msg}[/dim]")

    agent = ReActAgent(llm, executor, output=_show_step, conversation_mode=False)
    answer = agent.run(task, memory_manager=memory)

    if answer:
        console.print(Panel(answer, title="结果", border_style="green"))

    # Persist learnings from this task
    memory.conclude(task, answer)

    trace_manager.end_trace()


@app.command()
def version():
    """显示版本"""
    console.print("[bold]AgentNexus[/bold] v0.1.0")
