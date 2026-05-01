"""CLI chat command"""
import uuid

from . import app, console


@app.command()
def chat():
    """进入交互对话模式

    支持多行输入，空行提交问题。
    输入 /exit 或 /quit 退出，/help 查看帮助，/clear 清空当前输入。
    """
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
            "[dim]直接输入问题，空行提交。 /exit 退出 | /help 帮助 | /clear 清空[/dim]",
            border_style="cyan",
        )
    )

    llm = AgentLLM()
    executor = ToolExecutor()
    executor.registerTool("web_search", "搜索互联网获取实时信息，参数为搜索关键词", web_search)
    executor.registerTool("python_execute", "在安全沙箱中执行Python代码，参数为代码字符串", python_execute)

    agent = ReActAgent(llm, executor)

    while True:
        try:
            lines: list[str] = []
            console.print("\n[bold cyan]>>>[/bold cyan] ", end="")
            while True:
                try:
                    line = input()
                except EOFError:
                    console.print("\n[dim]再见[/dim]")
                    return

                trimmed = line.strip()

                if trimmed in ("/exit", "/quit"):
                    console.print("[dim]再见[/dim]")
                    return

                if trimmed == "/help":
                    console.print(
                        "\n[bold]可用命令:[/bold]\n"
                        "  直接输入问题，多行输入，空行提交\n"
                        "  [cyan]/exit[/cyan], [cyan]/quit[/cyan]  退出对话\n"
                        "  [cyan]/help[/cyan]              显示此帮助\n"
                        "  [cyan]/clear[/cyan]             清空当前输入\n"
                        "  [cyan]Ctrl+C[/cyan]             退出对话\n"
                    )
                    lines = []
                    console.print("[bold cyan]>>>[/bold cyan] ", end="")
                    continue

                if trimmed == "/clear":
                    lines = []
                    console.print("\n[dim]已清空当前输入[/dim]")
                    console.print("[bold cyan]>>>[/bold cyan] ", end="")
                    continue

                if trimmed == "" and lines:
                    break

                if trimmed == "" and not lines:
                    console.print("[bold cyan]>>>[/bold cyan] ", end="")
                    continue

                lines.append(line)
                console.print("... ", end="")

            question = "\n".join(lines)
            if not question.strip():
                continue

            session_id = f"chat_{uuid.uuid4().hex[:12]}"
            memory = MemoryManager(session_id)

            answer = agent.run(question, memory_manager=memory)
            if answer:
                console.print(Panel(answer, title="[bold]最终答案[/bold]", border_style="green"))
            else:
                console.print("[dim]Agent 未能得出答案。[/dim]")

        except KeyboardInterrupt:
            console.print("\n[dim]再见[/dim]")
            return
        except Exception as e:
            console.print(f"\n[red]错误: {e}[/red]")
