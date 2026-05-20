"""ChatScreen — main chat interface with real ReActAgent backend."""

import asyncio
import re
import threading
import time
from itertools import cycle

from rich.markdown import Markdown
from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.screen import Screen
from textual.widget import Widget
from textual.widgets import Input, Label, Static

from agentnexus.core.config import get_settings
from agentnexus.memory.short_term import ShortTermMemory
from agentnexus.observability.tracer import trace_manager
from agentnexus.tui.widgets.confirm_dialog import ConfirmDialog
from agentnexus.tui.widgets.hud import HUD
from agentnexus.tui.widgets.input_bar import InputBar
from agentnexus.tui.widgets.message import ChatMessage, ToolCall
from agentnexus.tui.widgets.side_panel import SidePanel


class ChatArea(Widget):
    """Scrollable message area."""

    DEFAULT_CSS = """
    ChatArea {
        height: 100%;
        overflow-y: auto;
    }
    """

    def add_message(self, role: str, content: str):
        self.mount(ChatMessage(role, content))
        self.call_after_refresh(self.scroll_end)

    def add_system(self, text: str):
        self.mount(ChatMessage("system", text, markup=True))
        self.call_after_refresh(self.scroll_end)

    def add_tool_call(self, name: str, result: str = "", duration_ms: float = 0):
        self.mount(ToolCall(name, result, duration_ms))
        self.call_after_refresh(self.scroll_end)

    def clear_all(self):
        self.remove_children()


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
        self._spinner_timer = None
        self._spinner_frames = None
        self._current_tool_name: str = ""
        self._current_tool_widget = None
        # Hook compact events for TUI visibility
        if self._memory:
            self._memory._on_compact = self._on_compact_event

    def compose(self) -> ComposeResult:
        yield Static(self._render_top_bar(), id="top-bar")
        self._chat_area = ChatArea(id="chat-area")
        self._side = SidePanel(id="side-panel")
        with Horizontal(id="middle"):
            yield self._chat_area
            yield self._side
        self._hud = HUD(id="hud")
        self._chat_input = InputBar(id="input-area")
        yield self._hud
        yield self._chat_input

    def _render_top_bar(self) -> str:
        model = getattr(self._agent, "model_id", "v4-flash") if self._agent else "v4-flash"
        branch = self._version.status().get("branch", "main") if self._version else "main"

        left = "[#fab283]●[/] [bold]AgentNexus[/]"
        center = f"[dim]会话:[/] {branch}  [dim]│[/]  [#6ba5f2]{model}[/]"
        right = "[dim]^H 帮助  ^L 清屏  Esc 输入[/]"

        return f"{left}  {center}  {right}"

    def on_mount(self):
        logo = (
            "[#fab283]"
            "┌─────────────────────────────────┐\n"
            "│  ⬡ AgentNexus                  │\n"
            "│  Task Orchestrator  │\n"
            "└─────────────────────────────────┘"
            "[/]"
        )
        self._chat_area.add_system(logo)
        self._chat_area.add_message("assistant", "欢迎使用 AgentNexus。输入问题开始，或 /help 查看命令。")
        self._refresh_version_display()
        self.call_after_refresh(lambda: self.query_one("#chat-input", Input).focus())

    # ── custom submit ──────────────────────────────────────────

    def on_input_bar_app_submit(self, event: InputBar.AppSubmit):
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
            "[/]命令: [/] /help  /undo  /redo  /log [--all]  /branch <名>\n"
            "       /checkout <ref>  /diff [ref1] [ref2]  /status\n"
            "       /clear [--all]  /stats"
        )

    def action_focus_input(self):
        try:
            self.query_one("#chat-input", Input).focus()
        except Exception:
            pass

    # ── version control helpers ──────────────────────────────────

    def _restore_stm_from_version(self):
        """Replace current STM with the version HEAD's snapshot."""
        if not self._version:
            return
        snapshot = self._version.get_head_stm()
        if snapshot and self._memory:
            new_stm = ShortTermMemory.from_json(snapshot)
            self._memory.short_term._messages = new_stm._messages
            self._memory.short_term._summary = new_stm._summary

    def _commit_if_answered(self, question: str, answer: str):
        """Auto-commit after a successful answer."""
        if not self._version or not self._memory:
            return
        stm_json = self._memory.short_term.to_json()
        self._version.commit(stm_json, question=question, answer=answer, new_ltm_ids=[])
        self._refresh_version_display()

    def _refresh_version_display(self):
        """Update top bar and side panel with current version state."""
        if not self._version:
            return
        st = self._version.status()
        self._side.update_version(
            st.get("branch", "main"),
            st["head"]["id"] if st.get("head") else "---",
            st.get("can_undo", False),
            st.get("can_redo", False),
        )
        # Refresh top bar
        try:
            tb = self.query_one("#top-bar", Static)
            tb.update(self._render_top_bar())
        except Exception:
            pass

    # ── commands ──────────────────────────────────────────────

    def _handle_command(self, text: str):
        parts = text.split(maxsplit=1)
        cmd = parts[0]
        arg = parts[1] if len(parts) > 1 else ""

        if cmd == "/help":
            self.action_show_help()
        elif cmd == "/clear":
            if arg.strip() == "--all" and self._version:
                self._version.reset()
                self._chat_area.clear_all()
                self._chat_area.add_system("[dim]已清除所有检查点和对话。[/]")
                self._refresh_version_display()
            else:
                self._chat_area.clear_all()
        elif cmd == "/undo" and self._version:
            prev = self._version.undo()
            if prev:
                self._restore_stm_from_version()
                self._chat_area.add_system(f"[dim]已回退至 [{prev['id']}][/]")
            else:
                self._chat_area.add_system("[dim]无可回退[/]")
            self._refresh_version_display()
        elif cmd == "/redo" and self._version:
            cp = self._version.redo()
            if cp:
                self._restore_stm_from_version()
                self._chat_area.add_system(f"[dim]已重做至 [{cp['id']}][/]")
            else:
                self._chat_area.add_system("[dim]无可重做[/]")
            self._refresh_version_display()
        elif cmd == "/log" and self._version:
            show_all = arg.strip() == "--all"
            entries = self._version.log(all_branches=show_all)
            if entries:
                lines = ["[bold]Checkpoints:[/]" + (" (全部)" if show_all else "")]
                for e in entries[:10]:
                    m = "[green]● HEAD[/]" if e.get("is_head") else ""
                    branch = f"[#a78bfa]{e.get('branch', '')}[/]" if e.get("branch") else ""
                    lines.append(f"  [dim]{e['id']}[/] {e.get('question', '')[:40]} {branch} {m}")
                self._chat_area.add_system("\n".join(lines))
            else:
                self._chat_area.add_system("[dim]暂无检查点[/]")
        elif cmd == "/status" and self._version:
            st = self._version.status()
            lines = [
                f"[bold]分支:[/] [#a78bfa]{st['branch']}[/]",
                f"[dim]HEAD: {st['head']['id'] if st['head'] else '---'}[/]",
                f"undo: {'[green]可用[/]' if st['can_undo'] else '[dim]不可用[/]'}  "
                f"redo: {'[green]可用[/]' if st['can_redo'] else '[dim]不可用[/]'}",
            ]
            self._chat_area.add_system("\n".join(lines))
            self._refresh_version_display()
        elif cmd == "/branch" and self._version:
            name = arg.strip()
            if not name:
                self._chat_area.add_system("[dim]用法: /branch <分支名>[/]")
                return
            cp = self._version.branch(name)
            self._restore_stm_from_version()
            self._chat_area.add_system(f"[green]已创建/切换至分支 [#a78bfa]{name}[/][/]")
            self._refresh_version_display()
        elif cmd == "/checkout" and self._version:
            ref = arg.strip()
            if not ref:
                self._chat_area.add_system("[dim]用法: /checkout <检查点ID | 分支名>[/]")
                return
            cp = self._version.checkout(ref)
            if cp:
                self._restore_stm_from_version()
                self._chat_area.add_system(f"[green]已切换至 [{cp['id']}][/]")
                self._refresh_version_display()
            else:
                self._chat_area.add_system(f"[dim]未找到: {ref}[/]")
        elif cmd == "/diff" and self._version:
            refs = arg.strip().split()
            r1 = refs[0] if len(refs) > 0 else None
            r2 = refs[1] if len(refs) > 1 else None
            result = self._version.diff(r1, r2)
            if result:
                lines = ["[bold]Diff:[/]"]
                for k, v in result.items():
                    if isinstance(v, list):
                        lines.append(f"  [dim]{k}:[/] {len(v)} 条变更")
                    else:
                        lines.append(f"  [dim]{k}:[/] {v}")
                self._chat_area.add_system("\n".join(lines))
            else:
                self._chat_area.add_system("[dim]无法比较（可能缺参数或无可比检查点）[/]")
        elif cmd == "/stats":
            self._chat_area.add_system(self._hud._build_text())
        else:
            self._chat_area.add_system(f"[dim]未知: {cmd}[/]")

    # ── spinner animation ────────────────────────────────────

    def _stop_spinner(self):
        if self._spinner_timer is not None:
            self._spinner_timer.stop()
            self._spinner_timer = None
        self._spinner_frames = None

    def _tick_spinner(self):
        if not self._current_tool_widget or not self._spinner_frames:
            return
        frame = next(self._spinner_frames)
        label = self._current_tool_widget.query_one("#tool-name", Label)
        label.update(f"{frame} {self._current_tool_widget.tool_name}")

    @staticmethod
    def _condense_search_result(text: str) -> str:
        """Show only title/score/URL from web_search; skip full content body.

        Input format (from web_search.py):
          [N] Title (date) [相关度: X.XX]
          URL: https://...
          <multi-line content body>
        """
        lines = text.split("\n")
        out = []
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            if re.match(r"^\[\d+\]", stripped) or stripped.startswith("URL:"):
                out.append(line)
        return "\n".join(out) if out else text[:500]

    @staticmethod
    def _condense_file_result(text: str) -> str:
        """Show only file metadata line from file_read; skip full file content.

        Input format (from file_ops.py):
          [文件] path (N 行, 共 X 字节)
          1 | line content...
          2 | line content...
        """
        first_line = text.split("\n")[0] if text else ""
        if first_line.startswith("[文件]"):
            return first_line
        return text[:200]

    # ── compact event handling ───────────────────────────────

    def _on_compact_event(self, event: dict):
        """Called from agent thread — forward to main thread for UI update."""
        self.app.call_from_thread(self._apply_compact_event, event)

    def _apply_compact_event(self, event: dict):
        e = event["event"]
        if e == "start":
            tokens = event.get("tokens_before", 0)
            self._hud.set_compacting(True)
            self._chat_area.add_system(f"[#fab283]上下文压缩[/] 当前 {tokens // 1000:,}k tokens...")
        elif e == "complete":
            self._hud.set_compacting(False)
            before = event.get("tokens_before", 0)
            after = event.get("tokens_after", 0)
            saved = before - after
            self._chat_area.add_system(
                f"[green]✓ 压缩完成[/] {before // 1000}k → {after // 1000}k ([dim]-{saved // 1000:,}k tokens[/])"
            )
            self._hud.update_context(current_tokens=after)
        elif e == "fail":
            self._hud.set_compacting(False)
            self._chat_area.add_system(f"[dim]压缩失败: {event.get('reason', '')}[/]")
        elif e == "circuit_open":
            self._hud.set_compacting(False)
            self._chat_area.add_system("[#e06c75]⚠ 熔断器打开[/] 连续压缩失败，切换为仅 microcompact 模式")
        elif e == "circuit_active":
            self._hud.set_compacting(False)
            tokens = event.get("tokens_after", 0)
            self._chat_area.add_system(f"[dim]熔断中 — 仅清理工具结果 (当前 {tokens // 1000:,}k tokens)[/]")
        elif e == "circuit_reset":
            self._hud.set_compacting(False)
            self._chat_area.add_system("[green]✓ 熔断器已重置[/] 恢复正常压缩能力")

    # ── agent execution ───────────────────────────────────────

    @work(exclusive=True)
    async def _run_agent(self, text: str):
        # ── Thread-safe confirmation bridge ──
        # agent thread calls _confirm(params) → this sets up a
        # threading.Event, pushes ConfirmDialog on the main Textual
        # thread, blocks until user responds, then returns True/False.
        def _tui_confirm(params_summary: str) -> bool:
            event = threading.Event()
            result_holder = [False]

            def _show_dialog():
                dialog = ConfirmDialog(
                    self._current_tool_name,
                    params_summary,
                    risk_level="high",
                )
                self.app.push_screen(dialog, callback=lambda confirmed: _on_result(confirmed))

            def _on_result(confirmed: bool):
                result_holder[0] = bool(confirmed)
                event.set()

            self.app.call_from_thread(_show_dialog)
            event.wait()  # block agent thread until user responds
            return result_holder[0]

        self._agent._confirm = _tui_confirm

        # ── Mount loading indicator ──
        loading = Static("[#fab283]● Working...[/]", id="loading-indicator")
        self._chat_area.mount(loading)
        self._chat_area.call_after_refresh(self._chat_area.scroll_end)

        def _on_output(msg: str):
            self.app.call_from_thread(_apply_output, msg)

        def _apply_output(msg: str):
            if msg.startswith("思考:"):
                self._chat_area.add_system(f"[#a78bfa]Thought:[/] [italic dim]{msg.replace('思考:', '').strip()}[/]")
            elif msg.startswith("行动:"):
                self._stop_spinner()
                tool_info = msg.removeprefix("行动:").strip()
                # Parse format: tool_name[params] or tool_name(params) (fallback)
                bracket = tool_info.find("[")
                paren = tool_info.find("(")
                if bracket >= 0:
                    tool_name = tool_info[:bracket].strip()
                elif paren >= 0:
                    tool_name = tool_info[:paren].strip()
                else:
                    tool_name = tool_info.strip()
                self._current_tool_name = tool_name
                widget = ToolCall(tool_name, result="执行中...")
                self._chat_area.mount(widget)
                self._current_tool_widget = widget
                self._chat_area.call_after_refresh(self._chat_area.scroll_end)
                self._spinner_frames = cycle(["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"])
                self._spinner_timer = self.set_interval(0.12, self._tick_spinner)

            elif msg.startswith("观察:"):
                self._stop_spinner()
                result = msg.removeprefix("观察:").strip()
                if self._current_tool_widget:
                    tool_lower = self._current_tool_widget.tool_name.strip().lower()
                    if tool_lower == "web_search":
                        result = self._condense_search_result(result)
                    elif tool_lower == "file_read":
                        result = self._condense_file_result(result)
                    self._current_tool_widget.update_result(result)
                    self._current_tool_widget = None
                else:
                    self._chat_area.add_system(f"[dim]观察: {result}[/]")
                if self._memory:
                    stm_tokens = self._memory.estimate_stm_tokens()
                    self._hud.update_context(current_tokens=stm_tokens)
            elif msg.startswith(("错误:", "警告:")):
                self._stop_spinner()
                self._current_tool_widget = None
                self._chat_area.add_system(f"[#e06c75]{msg}[/]")

        self._agent._output = _on_output

        def _run_with_trace():
            """Run agent in a traced context — each user input is its own trace."""
            trace_manager.configure(get_settings().traces_dir)
            ctx = trace_manager.start_trace(text)
            try:
                return self._agent.run(text, memory_manager=self._memory)
            finally:
                trace_manager.end_trace()

        try:
            answer = await asyncio.to_thread(_run_with_trace)
        except Exception as e:
            self._stop_spinner()
            self._current_tool_widget = None
            try:
                self._chat_area.query_one("#loading-indicator").remove()
            except Exception:
                pass
            self._chat_area.add_system(f"[#e06c75]错误: {e}[/]")
            self._running = False
            return

        # ── Remove loading indicator ──
        try:
            self._chat_area.query_one("#loading-indicator").remove()
        except Exception:
            pass

        if answer:
            # ── Streaming typing effect ──
            msg_widget = ChatMessage("assistant", "")
            self._chat_area.mount(msg_widget)
            self._chat_area.call_after_refresh(self._chat_area.scroll_end)

            msg_content = msg_widget.query_one("#msg-content", Static)

            # Time-throttled streaming: 20fps cap, update at sentence boundaries
            THROTTLE_MS = 0.05
            displayed = ""
            last_update = 0.0
            for char in answer:
                displayed += char
                now = time.monotonic()
                if now - last_update >= THROTTLE_MS and char in ".!?。！？\n":
                    msg_content.update(Text(displayed))
                    last_update = now
                    await asyncio.sleep(0.01)
            # Final flush
            if displayed:
                msg_content.update(Text(displayed))

            # Rich Markdown final render — parse off-thread to keep UI responsive
            loop = asyncio.get_running_loop()
            rendered = await loop.run_in_executor(None, Markdown, answer)
            msg_content.update(rendered)

            stm_tokens = self._memory.estimate_stm_tokens() if self._memory else 0
            usage = getattr(self._agent, "total_usage", None)
            if usage:
                self._hud.update_context(
                    current_tokens=stm_tokens,
                    total_input=usage.get("input_tokens", 0),
                    total_output=usage.get("output_tokens", 0),
                )
            # Auto-commit after successful answer
            self._commit_if_answered(text, answer)
        else:
            self._chat_area.add_system("[dim]Agent 未能得出答案。[/]")
        self._running = False
