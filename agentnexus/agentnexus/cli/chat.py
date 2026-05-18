"""CLI chat command — git-like versioned conversation, Enter to submit"""
import uuid

import typer
import warnings
warnings.filterwarnings("ignore", message=".*pkg_resources.*")

from . import app, console


def _max_ltm_id(memory) -> int:
    """Get the current maximum LTM ID for tracking new entries."""
    if memory.long_term is None:
        return 0
    try:
        row = memory.long_term._conn.execute(
            "SELECT MAX(id) as mx FROM long_term_memories"
        ).fetchone()
        return row["mx"] or 0
    except Exception:
        return 0


def _new_ltm_ids(memory, since_id: int) -> list[int]:
    """Get LTM IDs created after since_id."""
    if memory.long_term is None or since_id == 0:
        return []
    try:
        rows = memory.long_term._conn.execute(
            "SELECT id FROM long_term_memories WHERE id > ?", (since_id,)
        ).fetchall()
        return [r["id"] for r in rows]
    except Exception:
        return []


@app.command()
def chat(
    no_memory: bool = typer.Option(False, "--no-memory", help="禁用长期记忆存储（敏感会话）"),
):
    """进入交互对话模式

    直接输入问题，Enter 提交。
    /exit /quit  退出  |  /help  帮助  |  /clear  重置
    /undo  回退      |  /redo  重做  |  /log  历史
    /branch <name>   |  /checkout <ref>  |  /diff [ref1] [ref2]  |  /status
    """
    from prompt_toolkit import PromptSession
    from prompt_toolkit.styles import Style
    from rich.panel import Panel
    from rich.table import Table
    from rich import box

    from agentnexus.agents.re_act_agent import ReActAgent
    from agentnexus.tools.tool_executor import ToolExecutor
    from agentnexus.tools.web_search import web_search
    from agentnexus.tools.code_executor import python_execute
    from agentnexus.core.llm import AgentLLM
    from agentnexus.core.config import get_settings
    from agentnexus.memory.manager import MemoryManager
    from agentnexus.memory.short_term import ShortTermMemory
    from agentnexus.memory.versioned import ConversationVersionManager

    console.print(
        Panel(
            "[bold]AgentNexus 交互对话模式[/bold]\n"
            "[dim]直接输入问题，Enter 提交。"
            "/exit 退出 | /help 帮助 | /clear 重置 | /undo 回退 | /log 历史[/dim]",
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

    session_id = f"chat_{uuid.uuid4().hex[:12]}"
    memory = MemoryManager(session_id, llm=llm, enable_long_term=not no_memory)
    version = ConversationVersionManager(session_id, get_settings().memory_db_path)

    agent = ReActAgent(llm, executor, output=lambda msg: _show_step(console, msg))

    # ── helpers for version-aware STM restore ──────────────────────

    def _restore_stm_from_version():
        """Replace current STM with the version HEAD's snapshot."""
        snapshot = version.get_head_stm()
        if snapshot:
            new_stm = ShortTermMemory.from_json(snapshot)
            memory.short_term._messages = new_stm._messages
            memory.short_term._summary = new_stm._summary

    def _commit_if_answered(question: str, answer: str, ltm_before_id: int):
        """Auto-commit after a successful answer. ltm_before_id is max LTM id from before agent.run()."""
        stm_json = memory.short_term.to_json()
        new_ids = _new_ltm_ids(memory, ltm_before_id)
        cp_id = version.commit(stm_json, question=question, answer=answer,
                               new_ltm_ids=new_ids)
        return cp_id

    # ── main loop ─────────────────────────────────────────────────

    while True:
        try:
            text = session.prompt().strip()
            if not text:
                continue

            # ── built-in commands ─────────────────────────────────

            if text in ("/exit", "/quit"):
                console.print("[dim]再见[/dim]")
                return

            if text == "/help":
                console.print(
                    "\n[bold]可用命令:[/bold]\n"
                    "  [cyan]/exit[/cyan], [cyan]/quit[/cyan]   退出对话\n"
                    "  [cyan]/help[/cyan]               显示此帮助\n"
                    "  [cyan]/clear[/cyan]              重置短期记忆\n"
                    "  [cyan]/clear --all[/cyan]        清除所有记忆（含长期记忆和版本历史）\n"
                    "  [cyan]/undo[/cyan]               回退到上一轮对话\n"
                    "  [cyan]/redo[/cyan]               重做被撤销的对话\n"
                    "  [cyan]/log[/cyan]                查看对话历史 (--all 显示所有分支)\n"
                    "  [cyan]/branch[/cyan] <名称>      从当前位置创建分支\n"
                    "  [cyan]/checkout[/cyan] <ref>     切换到指定 checkpoint 或分支\n"
                    "  [cyan]/diff[/cyan] [ref1] [ref2] 对比两个 checkpoint\n"
                    "  [cyan]/status[/cyan]             显示当前分支和位置\n"
                )
                continue

            if text == "/clear":
                memory = MemoryManager(session_id, llm=llm)
                version = ConversationVersionManager(session_id, get_settings().memory_db_path)
                console.print("[dim]已重置短期记忆和版本历史[/dim]")
                continue

            if text == "/clear --all":
                memory.long_term.clear_all()
                version.reset()
                memory = MemoryManager(session_id, llm=llm)
                version = ConversationVersionManager(session_id, get_settings().memory_db_path)
                console.print("[dim]已清除所有记忆（短/长期记忆 + 版本历史）[/dim]")
                continue

            # ── version commands ──────────────────────────────────

            if text == "/undo":
                prev = version.undo()
                if prev:
                    _restore_stm_from_version()
                    console.print(
                        f"  [dim]已回退到 [{prev['id']}] {prev.get('question', '')[:40]}[/dim]"
                    )
                else:
                    console.print("  [dim]没有更早的记录了[/dim]")
                continue

            if text == "/redo":
                cp = version.redo()
                if cp:
                    _restore_stm_from_version()
                    console.print(
                        f"  [dim]已重做到 [{cp['id']}] {cp.get('question', '')[:40]}[/dim]"
                    )
                else:
                    console.print("  [dim]没有可重做的操作[/dim]")
                continue

            if text.startswith("/log"):
                all_branches = "--all" in text
                entries = version.log(all_branches=all_branches)
                if not entries:
                    console.print("  [dim]暂无对话记录[/dim]")
                else:
                    table = Table(title="对话历史", box=box.ROUNDED)
                    table.add_column("ID", style="dim")
                    table.add_column("问题", style="cyan")
                    table.add_column("分支", style="yellow")
                    table.add_column("标记")
                    for e in entries:
                        marker = "[green]HEAD[/green]" if e["is_head"] else ""
                        table.add_row(e["id"], e.get("question", "")[:50],
                                      e.get("branch_name", ""), marker)
                    console.print(table)
                continue

            if text.startswith("/branch"):
                parts = text.split(maxsplit=1)
                if len(parts) < 2:
                    console.print("  [yellow]用法: /branch <分支名>[/yellow]")
                else:
                    name = version.branch(parts[1])
                    console.print(f"  [dim]已创建分支 [bold]{name}[/bold][/dim]")
                continue

            if text.startswith("/checkout"):
                parts = text.split(maxsplit=1)
                if len(parts) < 2:
                    console.print("  [yellow]用法: /checkout <checkpoint_id 或 分支名>[/yellow]")
                else:
                    cp = version.checkout(parts[1])
                    if cp:
                        _restore_stm_from_version()
                        console.print(
                            f"  [dim]已切换到 [{cp['id']}] {cp.get('question', '')[:40]}"
                            f" (分支: {cp.get('branch_name', '')})[/dim]"
                        )
                    else:
                        console.print(f"  [red]未找到: {parts[1]}[/red]")
                continue

            if text.startswith("/diff"):
                parts = text.split()
                ref1 = parts[1] if len(parts) > 1 else ""
                ref2 = parts[2] if len(parts) > 2 else ""
                result = version.diff(ref1, ref2)
                if "error" in result:
                    console.print(f"  [yellow]{result['error']}[/yellow]")
                else:
                    console.print(
                        f"  [bold]对比 {result['ref1']} → {result['ref2']}[/bold]\n"
                        f"  STM 消息: +{result['stm_messages_added']}/"
                        f"-{result['stm_messages_removed']}\n"
                        f"  LTM 新增: {len(result['ltm_added'])} 条\n"
                        f"  LTM 删除: {len(result['ltm_removed'])} 条"
                    )
                continue

            if text == "/status":
                st = version.status()
                head = st["head"]
                console.print(
                    f"  会话:    {st['session_id']}\n"
                    f"  分支:    [bold]{st['branch']}[/bold]\n"
                    f"  HEAD:    {head['id'] if head else '(空)'} "
                    f"{head.get('question', '')[:40] if head else ''}\n"
                    f"  可回退:  {'[green]是[/green]' if st['can_undo'] else '[dim]否[/dim]'}\n"
                    f"  可重做:  {'[green]是[/green]' if st['can_redo'] else '[dim]否[/dim]'}"
                )
                continue

            # ── normal conversation turn ──────────────────────────

            ltm_before = _max_ltm_id(memory)
            answer = agent.run(text, memory_manager=memory)
            if answer:
                console.print(Panel(answer, title="[bold]最终答案[/bold]", border_style="green"))
                cp_id = _commit_if_answered(text, answer, ltm_before)
                console.print(f"  [dim]saved: [{cp_id}][/dim]")
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
            console.print(f"[dim]{traceback.format_exc()}[/dim]")
