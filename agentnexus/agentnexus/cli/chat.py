"""CLI chat command — 交互式多轮对话，Enter 提交"""
import typer
import warnings
warnings.filterwarnings("ignore", message=".*pkg_resources.*")

from . import app, console


@app.command()
def chat(
    no_memory: bool = typer.Option(False, "--no-memory", help="禁用长期记忆存储（敏感会话）"),
):
    """进入交互对话模式

    直接输入问题，Enter 提交。 /exit 退出 | /help 帮助 | /clear 重置
    """
    from prompt_toolkit import PromptSession
    from prompt_toolkit.styles import Style
    from rich.panel import Panel

    from agentnexus.agents.re_act_agent import ReActAgent
    from agentnexus.tools.tool_executor import ToolExecutor
    from agentnexus.tools.web_search import web_search
    from agentnexus.tools.code_executor import python_execute
    from agentnexus.core.llm import AgentLLM
    from agentnexus.memory.manager import MemoryManager

    console.print(
        Panel(
            "[bold]AgentNexus 交互对话模式[/bold]\n"
            "[dim]直接输入问题，Enter 提交。 /exit 退出 | /help 帮助 | /clear 重置[/dim]",
            border_style="cyan",
        )
    )

    session = PromptSession(
        style=Style.from_dict({"prompt": "bold cyan"}),
        message=[("class:prompt", ">>> ")],
    )

    raw_llm = AgentLLM()

    def _silent_think(messages, temperature=0):
        return raw_llm.think(messages, temperature, silent=True)

    class _SilentLLM:
        think = staticmethod(_silent_think)

    llm = _SilentLLM()

    executor = ToolExecutor()
    executor.registerTool("web_search", "搜索互联网获取实时信息，参数为搜索关键词", web_search)
    executor.registerTool("python_execute", "在安全沙箱中执行Python代码，参数为代码字符串", python_execute)

    def _show_step(c, msg: str):
        if msg.startswith("--- 第"):
            c.print(Panel(msg.strip("- "), border_style="dim", padding=(0, 2)))
        elif msg.startswith("思考:"):
            c.print(Panel(msg, border_style="blue", padding=(0, 2)))
        elif msg.startswith("行动:"):
            c.print(Panel(msg, border_style="yellow", padding=(0, 2)))
        elif msg.startswith("观察:"):
            c.print(Panel(msg, border_style="green", padding=(0, 2)))
        elif msg.startswith(("错误:", "警告:")):
            c.print(f"  [red]{msg}[/red]")
        elif "达到最大步数" in msg:
            c.print(f"  [dim]{msg}[/dim]")

    import uuid
    session_id = f"chat_{uuid.uuid4().hex[:12]}"
    memory = MemoryManager(session_id, llm=llm, enable_long_term=not no_memory)

    agent = ReActAgent(llm, executor, output=lambda msg: _show_step(console, msg))

    while True:
        try:
            text = session.prompt().strip()
            if not text:
                continue

            if text in ("/exit", "/quit"):
                console.print("[dim]再见[/dim]")
                return

            if text == "/help":
                console.print(
                    "\n[bold]可用命令:[/bold]\n"
                    "  [cyan]/exit[/cyan], [cyan]/quit[/cyan]  退出对话\n"
                    "  [cyan]/help[/cyan]              显示此帮助\n"
                    "  [cyan]/clear[/cyan]             重置会话记忆\n"
                    "  [cyan]Ctrl+C[/cyan]             退出对话\n"
                )
                continue

            if text == "/clear":
                memory = MemoryManager(session_id, llm=llm)
                console.print("[dim]已重置会话记忆[/dim]")
                continue

            answer = agent.run(text, memory_manager=memory)
            if answer:
                console.print(Panel(answer, title="[bold]最终答案[/bold]", border_style="green"))
            else:
                console.print("[dim]Agent 未能得出答案。[/dim]")

        except KeyboardInterrupt:
            console.print("\n[dim]再见[/dim]")
            return
        except EOFError:
            console.print("\n[dim]再见[/dim]")
            return
        except Exception as e:
            import traceback
            console.print(f"\n[red]错误: {e}[/red]")
            console.print(f"[dim]{traceback.format_exc()[:500]}[/dim]")
