"""ChatScreen — main chat interface with real ReActAgent backend."""

import asyncio

from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.message import Message
from textual.screen import Screen
from textual.widget import Widget
from textual.widgets import Input, Static

from agentnexus.tui.widgets.hud import HUD
from agentnexus.tui.widgets.message import ChatMessage, ToolCall
from agentnexus.tui.widgets.side_panel import SidePanel


class ChatArea(Widget):
    """Scrollable message area."""

    def add_message(self, role: str, content: str):
        self.mount(ChatMessage(role, content))
        self.call_after_refresh(self.scroll_end)

    def add_system(self, text: str):
        self.mount(ChatMessage("system", text))
        self.call_after_refresh(self.scroll_end)

    def add_tool_call(self, name: str, result: str = "", duration_ms: float = 0):
        self.mount(ToolCall(name, result, duration_ms))
        self.call_after_refresh(self.scroll_end)

    def clear_all(self):
        self.remove_children()


class ChatInput(Widget):
    """Input widget with styled prompt and focus-aware input."""

    class AppSubmit(Message):
        """Custom message when user submits text."""
        def __init__(self, text: str):
            super().__init__()
            self.text = text

    def compose(self) -> ComposeResult:
        self._inp = Input(placeholder="输入消息... (Enter 发送, /help 命令)", id="chat-input")
        yield Static(">", id="input-prompt")
        yield self._inp

    def on_input_submitted(self, event: Input.Submitted):
        if event.value.strip():
            self.post_message(self.AppSubmit(event.value.strip()))


class ChatScreen(Screen):
    """AgentNexus chat interface — OpenCode warm theme."""

    BINDINGS = [
        ("ctrl+l", "clear_screen", "清屏"),
        ("ctrl+h", "show_help", "帮助"),
        ("escape", "focus_input", "输入"),
    ]

    def __init__(self, agent, memory, version):
        super().__init__()
        self._agent = agent
        self._memory = memory
        self._version = version
        self._running = False

    def compose(self) -> ComposeResult:
        yield Static(self._render_top_bar(), id="top-bar")
        with Horizontal(id="main-area"):
            self._chat_area = ChatArea(id="chat-area")
            yield self._chat_area
            self._side = SidePanel(id="side-panel")
            yield self._side
        self._chat_input = ChatInput(id="input-area")
        yield self._chat_input
        self._hud = HUD(id="hud")
        yield self._hud

    def _render_top_bar(self) -> str:
        model = getattr(self._agent, 'model_id', 'v4-flash') if self._agent else 'v4-flash'
        branch = self._version.status().get("branch", "main") if self._version else "main"
        return (
            f" [#fab283]●[/] [bold]AgentNexus[/]"
            f"  [dim]│[/]  [dim]会话:[/] {branch}"
            f"  [dim]│[/]  [#6ba5f2]{model}[/]"
            f"  [dim]│[/]  [dim]^H 帮助  ^L 清屏  Esc 输入[/]"
        )

    def on_mount(self):
        self._chat_area.add_message("assistant",
            "欢迎使用 AgentNexus。输入问题开始，或 /help 查看命令。")
        self.call_after_refresh(lambda: self.query_one("#chat-input", Input).focus())

    # ── custom submit ──────────────────────────────────────────

    def on_chat_input_app_submit(self, event: ChatInput.AppSubmit):
        text = event.text
        inp = self.query_one("#chat-input", Input)
        inp.value = ""
        self._chat_area.add_message("user", text)
        if text.startswith("/"):
            self._handle_command(text)
        else:
            self._running = True
            self._run_agent(text)

    # ── keybindings ────────────────────────────────────────────

    def action_clear_screen(self):
        self._chat_area.clear_all()

    def action_show_help(self):
        self._chat_area.add_system(
            "/help  /undo  /redo  /log  /branch  /checkout\n"
            "/clear  /stats  /status  /audit  /diff  /memory"
        )

    def action_focus_input(self):
        try:
            self.query_one("#chat-input", Input).focus()
        except Exception:
            pass

    # ── commands ──────────────────────────────────────────────

    def _handle_command(self, text: str):
        parts = text.split(maxsplit=1)
        cmd = parts[0]

        if cmd == "/help":
            self.action_show_help()
        elif cmd == "/clear":
            self._chat_area.clear_all()
        elif cmd == "/undo" and self._version:
            prev = self._version.undo()
            self._chat_area.add_system(f"[dim]回退 [{prev['id']}][/]" if prev else "[dim]无可回退[/]")
        elif cmd == "/redo" and self._version:
            cp = self._version.redo()
            self._chat_area.add_system(f"[dim]重做 [{cp['id']}][/]" if cp else "[dim]无可重做[/]")
        elif cmd == "/log" and self._version:
            entries = self._version.log()
            if entries:
                lines = ["Checkpoints:"]
                for e in entries[:8]:
                    m = "[green]HEAD[/]" if e.get("is_head") else ""
                    lines.append(f"  [dim]{e['id']}[/] {e.get('question','')} {m}")
                self._chat_area.add_system("\n".join(lines))
            else:
                self._chat_area.add_system("[dim]暂无[/]")
        elif cmd == "/status" and self._version:
            st = self._version.status()
            self._chat_area.add_system(
                f"分支:{st['branch']} undo:{'y' if st['can_undo'] else 'n'} redo:{'y' if st['can_redo'] else 'n'}"
            )
        elif cmd == "/stats":
            self._chat_area.add_system(self._hud._build_text())
        else:
            self._chat_area.add_system(f"[dim]未知: {cmd}[/]")

    # ── agent execution ───────────────────────────────────────

    @work(exclusive=True)
    async def _run_agent(self, text: str):
        self._agent._confirm = lambda _: True

        def _on_output(msg: str):
            self.app.call_from_thread(_apply_output, msg)
        def _apply_output(msg: str):
            if msg.startswith("思考:"):
                self._chat_area.add_system(
                    f"[#a78bfa]●[/] [italic dim]{msg.replace('思考:','').strip()}[/]"
                )
            elif msg.startswith("行动:"):
                self._chat_area.add_system(
                    f"[#f5a742]⚙ {msg.replace('行动:','').strip()}[/]"
                )
            elif msg.startswith("观察:"):
                self._chat_area.add_system(
                    f"[dim]{msg.replace('观察:','').strip()}[/]"
                )
            elif msg.startswith(("错误:", "警告:")):
                self._chat_area.add_system(f"[#e06c75]{msg}[/]")

        self._agent._output = _on_output

        try:
            answer = await asyncio.to_thread(self._agent.run, text, memory_manager=self._memory)
        except Exception as e:
            self._chat_area.add_system(f"[#e06c75]错误: {e}[/]")
            self._running = False
            return

        if answer:
            self._chat_area.add_message("assistant", answer)
            self._hud.update_tokens(len(text) // 2, len(answer) // 2)
        else:
            self._chat_area.add_system("[dim]Agent 未能得出答案。[/]")
        self._running = False
