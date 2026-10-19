"""ChatScreen — main chat interface with real ReActAgent backend."""

import asyncio
import json
import logging
from functools import partial
from types import SimpleNamespace

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.events import Key
from textual.message import Message
from textual.screen import Screen
from textual.widget import Widget
from textual.widgets import Button, Label, Static

from agentnexus.core.config import get_settings
from agentnexus.core.text_utils import collapse_and_truncate
from agentnexus.memory.short_term import ShortTermMemory
from agentnexus.services.chat import ChatService, SessionHandle
from agentnexus.skills import (
    SkillEntry,
    SkillRegistry,
    format_tool_policy_summary,
    validate_session_profile,
)
from agentnexus.tui.commands import COMMAND_HELP, TerminalCommands
from agentnexus.tui.execution import ChatExecution
from agentnexus.tui.widgets.collaboration import CollaborationTree
from agentnexus.tui.widgets.confirm_dialog import ConfirmDialog
from agentnexus.tui.widgets.hud import HUD
from agentnexus.tui.widgets.input_bar import Composer, InputBar
from agentnexus.tui.widgets.message import ChatMessage, ReasoningBlock, ToolCall
from agentnexus.tui.widgets.side_panel import SidePanel
from agentnexus.tui.widgets.welcome import Welcome
from agentnexus.tui.workspace import ChatWorkspace

logger = logging.getLogger(__name__)

COMMAND_DEFINITIONS: tuple[tuple[str, str], ...] = (
    ("/help", "显示命令帮助"),
    ("/clear", "清屏；--all 同时清除检查点"),
    ("/undo", "回退到上一个检查点"),
    ("/redo", "重做到下一个检查点"),
    ("/log", "查看检查点日志"),
    ("/status", "查看状态"),
    ("/compact", "压缩当前上下文"),
    ("/stats", "查看运行统计"),
    ("/sessions", "列出历史会话"),
    ("/switch", "切换到指定会话"),
    ("/skill", "管理 Skill"),
    ("/mcp", "管理 MCP server"),
    ("/exit", "退出 TUI"),
)

COMMAND_SUBCOMMANDS: dict[str, tuple[str, ...]] = {
    "/skill": ("status", "list", "use", "enable", "disable", "default", "validate", "reset"),
    "/mcp": ("status", "tools", "resources", "prompts", "failures", "retry", "enable", "disable", "reload"),
    "/clear": ("--all",),
}


class ChatArea(Widget):
    """Scrollable message area."""
    DEFAULT_CSS = """
    ChatArea {
        height: 100%;
        overflow-y: auto;
    }
    """

    class OutputPending(Message):
        """Signal that new output arrived while the user reads earlier messages."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.following_output = True

    def compose(self):
        yield Welcome(id="welcome")
        tree = CollaborationTree(id="collaboration-tree")
        tree.display = False
        yield tree

    def watch_scroll_y(self, old_value, new_value):
        super().watch_scroll_y(old_value, new_value)
        if new_value < old_value and new_value < self.max_scroll_y:
            self.following_output = False
        elif new_value >= self.max_scroll_y:
            self.following_output = True

    def follow_output(self):
        if self.following_output:
            self.call_after_refresh(lambda: self.scroll_end(animate=False) if self.following_output else None)
        else:
            self.post_message(self.OutputPending())

    def resume_output(self):
        self.following_output = True
        self.call_after_refresh(lambda: self.scroll_end(animate=False))

    def scroll_home(self, *args, **kwargs):
        self.following_output = False
        return super().scroll_home(*args, **kwargs)

    def dismiss_welcome(self):
        self.query_one("#welcome").display = False

    def add_message(self, role: str, content: str):
        if self.is_mounted:
            self.dismiss_welcome()
        widget = ChatMessage(role, content)
        self.mount(widget)
        self.follow_output()
        return widget

    def add_system(self, text: str):
        if self.is_mounted:
            self.dismiss_welcome()
        self.mount(ChatMessage("system", text, markup=True))
        self.follow_output()

    def add_tool_call(self, name: str, result: str = "", duration_ms: float = 0):
        self.mount(ToolCall(name, result, duration_ms))
        self.follow_output()

    def clear_all(self):
        self.query("ChatMessage, ToolCall, ReasoningBlock").remove()
        self.following_output = True


class ChatScreen(ChatWorkspace, ChatExecution, Screen):
    """A terminal-first conversation and collaboration workspace."""

    BINDINGS = [
        Binding("ctrl+p", "command_menu", "操作", priority=True),
        Binding("f2", "session_menu", "会话", priority=True),
        Binding("f3", "toggle_inspector", "任务", priority=True),
        Binding("f4", "edit_message", "多行", priority=True),
        Binding("ctrl+c", "stop_task", "停止", priority=True),
        Binding("ctrl+end", "latest_output", "最新输出", priority=True),
        ("ctrl+l", "clear_screen", "清屏"),
        ("ctrl+h", "show_help", "帮助"),
    ]

    def __init__(self, agent, memory, version, mcp_manager=None, skill_service=None, capability_runtime=None, *, chat_service=None, session_id=None, runtime=None):
        super().__init__()
        self._agent = agent
        self._memory = memory
        self._version = version
        self._mcp_manager = mcp_manager
        self._skill_service = skill_service
        self._capability_runtime = capability_runtime
        self._spinner_timer = None
        self._spinner_frames = None
        self._current_tool_name: str = ""
        self._current_tool_widget = None
        self._current_tool_started_at = 0.0
        self._last_user_msg_widget = None
        self._turn_tool_names: list[str] = []
        self._turn_thought_count = 0
        self._skill_registry: SkillRegistry | None = None
        self._current_skill: SkillEntry | None = None
        self._skill_status = "idle"
        self._agent_worker = None
        self._streaming_msg_widget = None
        self._streaming_buffer = ""
        self._reasoning_widget = None
        self._run_transcripts = {}
        self._last_stream_update = 0.0
        self._runtime = runtime
        self._chat_service = chat_service if chat_service is not None else ChatService(
            agent_factory=lambda _sid=None: agent,
            memory_factory_builder=lambda _sid: lambda: memory,
            version_manager=version,
            skill_service=skill_service,
            tool_executor=getattr(agent, "tool_executor", None),
            capability_runtime=capability_runtime,
        )
        if session_id is not None:
            self._chat_session = self._chat_service.get_session_snapshot(session_id)["session"]
            self._skill_service = self._chat_service.session_skills(session_id)
        else:
            existing_id = getattr(version, "session_id", None)
            if isinstance(existing_id, str) and existing_id:
                self._chat_session = SessionHandle(existing_id, profile="tui")
            else:
                self._chat_session = self._chat_service.start_session()
            self._chat_service.attach_session(self._chat_session, agent=agent, memory=memory, version=version)
        self._current_run_id: str = ""
        self._current_turn = None
        self._closing = False
        self._active_runs = {}
        self._running_sessions = set()
        self._compacting_sessions = set()
        self._queued_messages = {}
        self._submissions = {}
        self._attachments = {}
        self._drafts = {}
        self._tool_widgets = {}
        self._seen_collaboration_messages = set()
        command_runtime = runtime if runtime is not None else SimpleNamespace(
            chat=self._chat_service, agent=agent, memory_manager=memory, version_manager=version,
            llm=getattr(agent, "llm_client", None), mcp_manager=mcp_manager,
            capability_runtime=capability_runtime, wiki_service=None,
        )
        self._terminal = TerminalCommands(command_runtime)
        self._terminal.load_draft(self._chat_session.id)
        self._drafts = self._terminal.inputs
        self._attachments = self._terminal.attachments
        self._submissions = self._terminal.submissions
        self._command_results = {}
        if self._memory:
            self._memory._on_compact = partial(self._on_compact_event, session_id=self._chat_session.id)

    def compose(self) -> ComposeResult:
        yield Static(self._render_top_bar(), id="top-bar")
        self._chat_area = ChatArea(id="chat-area")
        with Horizontal(id="middle"):
            yield self._chat_area
            self._side_panel = SidePanel(id="side-panel")
            yield self._side_panel
        self._command_palette_content = Static("", id="command-palette-content")
        with VerticalScroll(id="command-palette"):
            yield self._command_palette_content
        latest = Button("有新输出 · Ctrl+End 跳到最新", id="new-output")
        latest.display = False
        yield latest
        with Horizontal(id="task-strip"):
            yield Static("", id="task-summary")
            yield Button("撤销", id="undo-checkpoint")
            yield Button("重做", id="redo-checkpoint")
            yield Button("记录", id="open-task-record")
        self._hud = HUD(id="hud", compact=True)
        self._chat_input = InputBar(id="input-area", hud=self._hud)
        yield self._chat_input

    def _render_top_bar(self) -> str:
        branch = self._version.status().get("branch", "main") if self._version else "main"
        from pathlib import Path

        from rich.markup import escape

        workspace = self._chat_session.workspace or str(Path.cwd())
        name = Path(workspace).name or workspace
        return f"[bold]AgentNexus[/]  [dim]{escape(name)} · {escape(str(branch))} · 会话 {escape(self._chat_session.id[:8])}[/]"

    def on_mount(self):
        self._render_restored_history()
        self._update_welcome()
        self.query_one("#chat-input", Composer).value = self._drafts.get(self._chat_session.id, "")
        self._init_skill_registry()
        self._refresh_version_display()
        if hasattr(self, "_side_panel"):
            self._refresh_skill_panel()
            self._refresh_model_panel()
            self._refresh_todo_panel()
            self._refresh_tools_panel()
            self._refresh_mcp_panel()
        if hasattr(self, '_hud') and self._agent:
            try:
                self._refresh_session_metrics()
            except Exception as e:
                logger.debug("Failed to update HUD capabilities: %s", e)
        self.call_after_refresh(lambda: self.query_one("#chat-input", Composer).focus())
        self.call_after_refresh(self._refresh_workspace)
        self.set_interval(1, self._refresh_workspace)
        self._load_recent_session()

    def _update_welcome(self):
        welcome = self._chat_area.query_one("#welcome", Welcome)
        welcome.model = str(getattr(self._agent, "model_id", "未配置"))
        welcome.workspace = self._chat_session.workspace or ""
        welcome.thinking = self._chat_service.get_thinking_effort(self._chat_session.id) or "默认"
        welcome.display = not bool(self._chat_area.query(ChatMessage))
        welcome.refresh(layout=True)

    def _render_restored_history(self):
        messages = None
        if self._version is not None:
            history = self._version.get_messages_with_answers(limit=0)
            if isinstance(history, list) and history:
                messages = history
        try:
            if messages is None:
                if not self._memory or not getattr(self._memory, "short_term", None):
                    return
                messages = self._memory.short_term.get_all()
        except Exception as e:
            logger.debug("Failed to get messages for history restore: %s", e)
            return
        visible = [m for m in messages if m.get("role") in {"system", "user", "assistant", "tool"}]
        if not visible:
            return
        self._chat_area.add_system(f"[dim]Restored {len(visible)} messages from this session.[/]")
        for msg in visible:
            role = msg.get("role", "")
            content = str(msg.get("content", "") or "")
            if not content:
                continue
            if role == "system" and content.startswith("[思考过程]"):
                self._chat_area.mount(ReasoningBlock(content.removeprefix("[思考过程]").strip()))
            elif role == "system" and content.startswith("[最终答案]"):
                self._chat_area.add_message("assistant", content.removeprefix("[最终答案]").strip())
            elif role == "tool":
                tool_name = ""
                tool_result = content
                if content.startswith("Action: "):
                    parts = content.split("\n", 1)
                    if len(parts) > 1:
                        action_part = parts[0]
                        tool_result = parts[1].replace("Observation: ", "", 1)
                        bracket_idx = action_part.find("[")
                        if bracket_idx > 0:
                            tool_name = action_part[len("Action: "):bracket_idx].strip()
                self._chat_area.add_tool_call(tool_name or "tool", tool_result)
            elif role == "system":
                self._chat_area.add_system(content)
            else:
                self._chat_area.add_message(role, content)

    def on_input_bar_app_submit(self, event: InputBar.AppSubmit):
        text = event.text
        inp = self.query_one("#chat-input", Composer)
        inp.value = ""
        self._terminal.save_draft(self._chat_session.id, text="")
        if self._terminal.drafts.get(self._chat_session.id) == text:
            self._terminal.drafts.pop(self._chat_session.id, None)
        self._update_command_suggestions("")
        display_text = text
        setting = text.split(maxsplit=3)
        if len(setting) == 4 and setting[:2] == ["/settings", "set"] and setting[2].endswith("_api_key"):
            display_text = f"/settings set {setting[2]} ****"
        self._last_user_msg_widget = self._chat_area.add_message("user", display_text)
        if text.startswith("/"):
            self._handle_command(text)
        else:
            sid = self._chat_session.id
            self._submit_session_message({"text": text, "attachments": self._attachments.pop(sid, None)}, sid)
        self._refresh_workspace()

    def on_input_bar_app_input_changed(self, event: InputBar.AppInputChanged):
        self._update_command_suggestions(event.text)
        self._terminal.save_draft(self._chat_session.id, text=event.text)

    def on_key(self, event: Key):
        if event.key != "escape":
            return
        self.remove_class("has-inspector")
        self._update_command_suggestions("")
        event.stop()
        self.action_focus_input()

    def action_stop_task(self):
        self._force_interrupt_agent()

    def on_chat_area_output_pending(self):
        if not self._chat_area.following_output:
            self.query_one("#new-output").display = True

    def action_latest_output(self):
        self.query_one("#new-output").display = False
        self._chat_area.resume_output()

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "new-output":
            self.action_latest_output()
        elif event.button.id in {"open-task-record", "open-collaboration", "open-inspector"}:
            self.action_task_record()
        elif event.button.id in {"undo-checkpoint", "redo-checkpoint"}:
            self._handle_command("/undo" if event.button.id == "undo-checkpoint" else "/redo")

    def action_clear_screen(self):
        self._chat_area.clear_all()

    def action_show_help(self):
        commands = "  ".join(command for command, _ in COMMAND_DEFINITIONS)
        self._chat_area.add_system(
            f"[dim]命令:[/] {commands}\n"
            "       /skill [status|list|use <id> [--default]|enable <id>|disable <id>|"
            "default <id>|validate [id]|reset]\n"
            "       /mcp [status|tools|resources|prompts|failures|retry|enable|disable|reload]"
        )
        self._chat_area.add_message("system", "\n".join(f"{command} {usage}" for command, usage in COMMAND_HELP.items()))

    def action_focus_input(self):
        try:
            self.query_one("#chat-input", Composer).focus()
        except Exception as e:
            logger.debug("Failed to focus chat input: %s", e)

    def _restore_stm_from_version(self):
        """Replace current STM with the version HEAD's snapshot."""
        if not self._version:
            return
        snapshot = self._version.get_head_stm()
        if snapshot and self._memory:
            self._memory.short_term = ShortTermMemory.from_json(snapshot)

    def _commit_if_answered(self, question: str, answer: str):
        """Auto-commit after a successful answer."""
        turn = getattr(self, "_current_turn", None)
        if turn is not None:
            turn.finish(answer)
            self._refresh_version_display()

    def _refresh_version_display(self):
        """Update top bar and side panel with current version state."""
        if not self._version:
            return
        st = self._version.status()
        self._hud.update_version(
            st["head"]["id"] if st.get("head") else "---",
            st.get("can_undo", False),
            st.get("can_redo", False),
        )
        try:
            self._side_panel.update_version(
                st["head"]["id"] if st.get("head") else "---",
                st.get("can_undo", False),
                st.get("can_redo", False),
            )
        except Exception as e:
            logger.debug("Failed to update side panel version: %s", e)
        try:
            tb = self.query_one("#top-bar", Static)
            tb.update(self._render_top_bar())
        except Exception as e:
            logger.debug("Failed to refresh top bar: %s", e)

    def _handle_command(self, text: str):
        parts = text.split(maxsplit=1)
        cmd = parts[0]
        arg = parts[1] if len(parts) > 1 else ""

        if cmd == "/theme":
            if arg in {"nexus-dark", "nexus-light", "ansi-dark", "ansi-light"}:
                self.app.theme = arg
            else:
                self._chat_area.add_message("system", "主题: nexus-dark / nexus-light / ansi-dark / ansi-light")
            return

        if cmd in COMMAND_HELP:
            self._run_terminal_command(cmd, arg, self._chat_session.id)
            return

        if cmd == "/help":
            self.action_show_help()
        elif cmd == "/exit":
            self.app.exit()
        elif cmd == "/clear":
            if arg.strip() == "--all" and self._version:
                self._run_terminal_command("/reset", "", self._chat_session.id)
            else:
                self._chat_area.clear_all()
        elif cmd == "/log" and self._version:
            entries = self._version.log()
            if entries:
                lines = ["[bold]Checkpoints:[/]"]
                for e in entries[:10]:
                    m = "[green]● HEAD[/]" if e.get("is_head") else ""
                    lines.append(f"  [dim]{e['id']}[/] {e.get('question', '')[:40]} {m}")
                self._chat_area.add_system("\n".join(lines))
            else:
                self._chat_area.add_system("[dim]暂无检查点[/]")
        elif cmd == "/status" and self._version:
            st = self._version.status()
            lines = [
                f"[dim]HEAD: {st['head']['id'] if st['head'] else '---'}[/]",
                f"undo: {'[green]可用[/]' if st['can_undo'] else '[dim]不可用[/]'}  "
                f"redo: {'[green]可用[/]' if st['can_redo'] else '[dim]不可用[/]'}",
            ]
            self._chat_area.add_system("\n".join(lines))
            self._refresh_version_display()
        elif cmd == "/sessions":
            self._handle_sessions()
        elif cmd == "/switch":
            self._handle_switch(arg)
        elif cmd == "/mcp":
            self._handle_mcp_command(arg)
        elif cmd == "/skill":
            self._handle_skill_command(arg)
        else:
            if self._handle_dynamic_skill_command(cmd, arg, short_form=True):
                return
            if self._handle_dynamic_skill_command(cmd, arg):
                return
            self._chat_area.add_system(f"[dim]未知: {cmd}[/]")

    def _handle_sessions(self):
        """List recent sessions for the current workspace."""
        if not self._version:
            self._chat_area.add_system("[red]版本管理未启用[/]")
            return

        from pathlib import Path

        from agentnexus.core.config import get_settings
        from agentnexus.memory.versioned import ConversationVersionManager

        settings = get_settings()
        workspace = str(Path.cwd())

        sessions = ConversationVersionManager.find_recent_sessions(
            settings.memory_db_path, workspace, limit=10
        )

        if not sessions:
            self._chat_area.add_system("[yellow]当前目录下没有找到历史会话[/]")
            return

        self._chat_area.add_system("[bold]最近的会话:[/]")
        for i, s in enumerate(sessions, 1):
            sid = s["session_id"]
            preview = s.get("preview", "") or "(空)"
            if len(preview) > 50:
                preview = preview[:47] + "..."
            last_msg = s.get("last_message_at", "") or s.get("updated_at", "")
            marker = " [green]← 当前[/]" if sid == self._version.session_id else ""
            self._chat_area.add_system(f"  {i}. [cyan]{sid}[/] ({last_msg}){marker}")
            self._chat_area.add_system(f"     {preview}")

        self._chat_area.add_system("\n[dim]使用 /switch <session_id> 切换会话[/]")

    def _handle_switch(self, arg: str):
        """Switch every session-owned resource together."""
        if not arg.strip():
            self._chat_area.add_system("[red]用法: /switch <session_id>[/]")
            return
        if self._version is None:
            self._chat_area.add_system("[red]版本管理未启用[/]")
            return
        try:
            result = self._terminal.run("/session", "switch " + arg.strip(), self._chat_session.id)
            self._activate_session(result.session_id)
            self._chat_area.add_message("system", f"已切换到会话: {result.session_id}")
        except (KeyError, ValueError) as exc:
            self._chat_area.add_message("system", str(exc))

    @work(group="terminal-commands")
    async def _run_terminal_command(self, command, args, sid, *, presentation="chat"):
        try:
            if self._terminal.requires_confirmation(command, args):
                approved = await self.app.push_screen_wait(ConfirmDialog(command, f"会话 {sid}\n{command} {args}", risk_level="high"))
                if not approved:
                    self._chat_area.add_message("system", "操作已取消")
                    return
            result = await asyncio.to_thread(self._terminal.run, command, args, sid)
            rendered = json.dumps(result.value, ensure_ascii=False, indent=2, default=str)
            self._command_results.setdefault(sid, []).append(rendered)
            if result.draft is not None:
                self._terminal.save_draft(sid, text=result.draft)
            if result.submission is not None:
                submission = dict(result.submission)
                if sid == self._chat_session.id:
                    self._chat_area.add_message("user", submission["text"])
                self._submit_session_message(submission, sid)
            if sid != self._chat_session.id:
                self.app.notify(f"会话 {sid} 的 {command} 已完成")
                return
            if command in {"/undo", "/redo", "/reset"}:
                self._chat_area.clear_all()
                self._render_restored_history()
                self._refresh_version_display()
            if presentation == "record":
                from agentnexus.tui.screens.interactions import RecordScreen

                action = args.split(maxsplit=1)[0] if args.strip() else ""
                changed = command in {"/think", "/plan", "/undo", "/redo"} or (command, action) in {
                    ("/model", "select"), ("/attach", "add"), ("/attach", "remove"),
                    ("/session", "new"), ("/session", "switch"), ("/recover", "continue"), ("/recover", "save"),
                }
                if changed:
                    self.app.notify(f"{command} 已完成")
                else:
                    self.app.push_screen(RecordScreen(command + " · 操作结果", rendered), lambda _: self.action_focus_input())
            else:
                self._chat_area.add_message("system", rendered)
            if result.session_id is not None:
                self._activate_session(result.session_id)
            if result.draft is not None:
                self.query_one("#chat-input", Composer).value = result.draft
            self._refresh_model_panel()
            self._refresh_workspace()
        except Exception as exc:
            failure = f"{command} 失败: {exc}"
            if sid == self._chat_session.id:
                self._chat_area.add_message("system", failure)
            else:
                self._command_results.setdefault(sid, []).append(failure)
                self.app.notify(f"会话 {sid} 的 {command} 失败")

    def _activate_session(self, session_id):
        old_id = self._chat_session.id
        self._drafts[old_id] = self.query_one("#chat-input", Composer).value
        self._terminal.save_draft(old_id)
        self._terminal.load_draft(session_id)
        self._chat_session = self._chat_service.get_session_snapshot(session_id)["session"]
        self._agent = self._chat_service.session_agent(session_id)
        self._memory = self._chat_service.session_memory(session_id)
        self._version = self._chat_service.session_version(session_id)
        self._skill_service = self._chat_service.session_skills(session_id)
        self._current_skill = None
        self._skill_status = "idle"
        self._current_run_id = self._active_runs.get(session_id, "")
        self._streaming_msg_widget = None
        self._streaming_buffer = ""
        self._reasoning_widget = None
        self._last_user_msg_widget = None
        self._turn_tool_names = []
        self._turn_thought_count = 0
        if self._memory:
            self._memory._on_compact = partial(self._on_compact_event, session_id=session_id)
        self._hud.set_compacting(session_id in self._compacting_sessions)
        self._chat_area.clear_all()
        self._render_restored_history()
        if session_id in self._running_sessions:
            question = self._submissions.get(session_id, {}).get("text", "")
            user_messages = list(self._chat_area.query("ChatMessage.user"))
            if question and (not user_messages or user_messages[-1].content != question):
                self._chat_area.add_message("user", question)
            for event in self._run_transcripts.get(session_id, []):
                self._apply_service_event(event, replay=True)
        self.call_after_refresh(self._update_welcome)
        self._load_recent_session()
        self.query_one("#new-output").display = False
        self._side_panel.update_timeline([])
        self.query_one("#chat-input", Composer).value = self._drafts.get(session_id, "")
        self._init_skill_registry()
        self._refresh_version_display()
        self._refresh_model_panel()
        self._refresh_skill_panel()
        self._refresh_todo_panel()
        self._refresh_session_metrics()
        for output in self._command_results.pop(session_id, []):
            self._chat_area.add_message("system", output)
        self._refresh_workspace()

    def _update_command_suggestions(self, text: str):
        suggestions = self._match_command_suggestions(text)
        try:
            palette = self.query_one("#command-palette", VerticalScroll)
            self.query_one("#command-palette-content", Static).update(suggestions)
            palette.styles.display = "block" if suggestions else "none"
            if suggestions:
                palette.scroll_home(animate=False)
        except Exception as e:
            logger.debug("Failed to update command suggestions: %s", e)

    def _match_command_suggestions(self, text: str) -> str:
        raw = text.strip()
        if not raw.startswith("/"):
            return ""
        prefix = raw.lower()
        matches = []
        needle = prefix[1:]
        for command, description, aliases in self._command_catalog():
            command_lower = command.lower()
            if " " in prefix and not command_lower.startswith(prefix.split(maxsplit=1)[0] + " "):
                continue
            alias_matches = needle and " " not in prefix and any(alias.startswith(needle) for alias in aliases)
            if command_lower.startswith(prefix) or alias_matches:
                matches.append((command, description))
        if not matches:
            return "[dim]无匹配命令[/]"
        rendered = "\n".join(f"  [#70a6e8]{command:<18}[/] [dim]{description}[/]" for command, description in matches)
        return "[bold]Commands[/]\n" + rendered

    def _command_catalog(self) -> list[tuple[str, str, tuple[str, ...]]]:
        commands = [(command, description, ()) for command, description in COMMAND_DEFINITIONS]
        commands.extend((command, usage, ()) for command, usage in COMMAND_HELP.items() if command not in {item[0] for item in commands})
        for command, subcommands in COMMAND_SUBCOMMANDS.items():
            for subcommand in subcommands:
                commands.append((f"{command} {subcommand}", "子命令", (subcommand.lower(),)))
        for qualified_id, display_name, _description in self._available_skill_summary():
            short_id = qualified_id.rsplit("/", 1)[-1].replace("_", "-")
            aliases = self._skill_command_aliases(qualified_id, display_name, _description)
            commands.append((f"/{short_id}", f"Skill: {display_name}", aliases))
            legacy = f"/{short_id}-skill"
            if legacy != f"/{short_id}":
                commands.append((legacy, f"Skill: {display_name}", aliases))
        return commands

    @staticmethod
    def _skill_command_aliases(qualified_id: str, display_name: str, description: str) -> tuple[str, ...]:
        text = f"{qualified_id} {display_name} {description}".lower()
        aliases = set()
        if any(word in text for word in ("doc", "docx", "pdf", "document", "word")):
            aliases.update({"d", "doc", "document"})
        if any(word in text for word in ("image", "photo", "picture")):
            aliases.update({"i", "image"})
        if any(word in text for word in ("sheet", "spreadsheet", "excel", "xlsx", "csv")):
            aliases.update({"s", "sheet"})
        if any(word in text for word in ("slide", "ppt", "pptx", "presentation")):
            aliases.update({"p", "ppt", "presentation"})
        return tuple(sorted(aliases))

    def _init_skill_registry(self):
        if self._skill_service is not None:
            try:
                self._skill_registry = self._skill_service.registry
                snapshot = self._skill_service.snapshot()
                self._skill_status = snapshot.status
                self._current_skill = getattr(self._skill_service, "current", None)
                return
            except Exception as e:
                logger.debug("Failed to init skill registry from service: %s", e)
        try:
            self._skill_registry = SkillRegistry.from_settings(get_settings())
            self._skill_registry.discover()
            self._skill_status = "error" if self._skill_registry.errors else "idle"
            if self._skill_registry.errors:
                self._chat_area.add_system(self._format_skill_errors(self._skill_registry))
        except Exception as exc:
            self._skill_registry = None
            self._skill_status = "error"
            try:
                self._chat_area.add_system(f"[dim]Skill registry 初始化失败: {exc}[/]")
            except Exception as e:
                logger.debug("Failed to display skill registry error: %s", e)

    def _handle_skill_command(self, arg: str):
        if self._skill_registry is None:
            self._init_skill_registry()
        registry = self._skill_registry
        if registry is None:
            self._chat_area.add_system("[dim]Skill registry 不可用。[/]")
            self._refresh_skill_panel()
            return

        parts = arg.strip().split(maxsplit=1)
        subcmd = parts[0] if parts else "status"
        rest = parts[1] if len(parts) > 1 else ""

        try:
            if subcmd == "status":
                self._sync_skill_service_state()
                applied = getattr(self._agent, "session_profile", None) is not None
                default_skill = getattr(get_settings(), "default_skill", "")
                skill_snapshot = self._skill_service.snapshot() if self._skill_service is not None else None
                self._chat_area.add_system(
                    self._format_skill_status(
                        registry,
                        self._current_skill,
                        self._skill_status,
                        applied=applied,
                        default_skill=default_skill,
                        runtime=skill_snapshot,
                    )
                )
            elif subcmd == "list":
                if self._skill_service is not None:
                    self._skill_service.refresh()
                else:
                    registry.discover()
                self._chat_area.add_system(self._format_skill_list(registry))
                self._skill_status = "error" if registry.errors else (
                    "selected" if self._current_skill is not None else "idle"
                )
                self._refresh_skill_panel()
            elif subcmd in {"search", "show"}:
                if subcmd == "show":
                    entry = registry.get(rest.strip())
                    if entry is None:
                        raise ValueError(f"未找到 skill: {rest}")
                    value = {"id": entry.qualified_id, "path": str(entry.path), "workflow": entry.workflow.model_dump()}
                else:
                    needle = rest.casefold()
                    value = [{"id": entry.qualified_id, "name": entry.display_name, "description": entry.description}
                             for entry in registry.list() if needle in f"{entry.qualified_id} {entry.display_name} {entry.description}".casefold()]
                self._chat_area.add_message("system", json.dumps(value, ensure_ascii=False, indent=2, default=str))
            elif subcmd == "validate":
                if self._skill_service is not None:
                    self._skill_service.refresh()
                    errors = self._skill_service.validate(rest.strip() or None)
                    self._skill_status = self._skill_service.snapshot().status
                else:
                    registry.discover()
                    errors = registry.validate(rest.strip() or None)
                    self._skill_status = "error" if errors else (
                        "selected" if self._current_skill is not None else "idle"
                    )
                self._chat_area.add_system(self._format_skill_validation(registry, errors, rest.strip() or None))
                self._refresh_skill_panel()
            elif subcmd in {"enable", "disable"}:
                target = rest.strip()
                if not target:
                    self._chat_area.add_system("[dim]用法: /skill enable|disable <skill_id>[/]")
                    return
                if self._capability_runtime is not None:
                    result = (
                        self._capability_runtime.enable("skills", target)
                        if subcmd == "enable"
                        else self._capability_runtime.disable("skills", target)
                    )
                    self._chat_area.add_system(f"[dim]Skill {subcmd}: {target} - {result.get('skills')}[/]")
                elif self._skill_service is not None:
                    self._skill_service.set_enabled(target, subcmd == "enable")
                    self._chat_area.add_system(f"[dim]Skill {subcmd}: {target}[/]")
                self._refresh_skill_panel()
            elif subcmd == "use":
                target, persist_default = self._parse_skill_use_args(rest)
                if not target:
                    self._chat_area.add_system(
                        "[dim]用法: /skill use <skill_id | namespace/skill_id> [--default][/]"
                    )
                    return
                try:
                    entry = registry.get(target)
                except ValueError as exc:
                    self._skill_status = "error"
                    self._refresh_skill_panel()
                    self._chat_area.add_system(f"[dim]{exc}[/]")
                    return
                if entry is None:
                    self._chat_area.add_system(f"[dim]未找到 skill: {target}[/]")
                    return
                profile = entry.workflow.to_session_profile()
                if self._skill_service is not None:
                    entry = self._skill_service.use(target)
                    self._skill_status = self._skill_service.snapshot().status
                else:
                    validate_session_profile(profile)
                    if hasattr(self._agent, "set_session_profile"):
                        self._agent.set_session_profile(profile)
                    self._skill_status = "selected"
                self._current_skill = entry
                if persist_default:
                    self._persist_default_skill(entry.qualified_id)
                self._refresh_skill_panel()
                self._chat_area.add_system(
                    f"[green]已选择 skill[/] {entry.qualified_id} [dim]({entry.display_name}; "
                    f"{format_tool_policy_summary(profile.tool_policy)})[/]"
                    + (" [dim]已设为默认[/]" if persist_default else "")
                )
            elif subcmd == "default":
                target = rest.strip()
                if target in {"", "reset", "none", "default"}:
                    self._clear_default_skill()
                    if self._skill_service is not None:
                        self._skill_service.reset()
                    elif hasattr(self._agent, "set_session_profile"):
                        self._agent.set_session_profile(None)
                    self._current_skill = None
                    self._skill_status = "idle"
                    self._refresh_skill_panel()
                    self._chat_area.add_system("[dim]默认 skill 已清除，当前会话已恢复默认。[/]")
                    return
                try:
                    entry = registry.get(target)
                except ValueError as exc:
                    self._skill_status = "error"
                    self._refresh_skill_panel()
                    self._chat_area.add_system(f"[dim]{exc}[/]")
                    return
                if entry is None:
                    self._chat_area.add_system(f"[dim]未找到 skill: {target}[/]")
                    return
                profile = entry.workflow.to_session_profile()
                if self._skill_service is not None:
                    entry = self._skill_service.use(target)
                    self._skill_status = self._skill_service.snapshot().status
                else:
                    validate_session_profile(profile)
                    if hasattr(self._agent, "set_session_profile"):
                        self._agent.set_session_profile(profile)
                    self._skill_status = "selected"
                self._current_skill = entry
                self._persist_default_skill(entry.qualified_id)
                self._refresh_skill_panel()
                self._chat_area.add_system(f"[green]默认 skill 已设置[/] {entry.qualified_id}")
            elif subcmd == "reset":
                if self._skill_service is not None:
                    self._skill_service.reset()
                elif hasattr(self._agent, "set_session_profile"):
                    self._agent.set_session_profile(None)
                self._current_skill = None
                self._skill_status = "idle"
                self._refresh_skill_panel()
                self._chat_area.add_system("[dim]已恢复默认 skill/workflow。[/]")
            else:
                self._chat_area.add_system(
                    "[dim]用法: /skill [status|list|use <id> [--default]|default <id>|validate [id]|reset][/]"
                )
        except Exception as exc:
            self._skill_status = "error"
            self._refresh_skill_panel()
            self._chat_area.add_system(f"[dim]Skill 命令失败: {exc}[/]")

    @staticmethod
    def _parse_skill_use_args(rest: str) -> tuple[str, bool]:
        parts = rest.strip().split()
        persist_default = "--default" in parts
        target_parts = [part for part in parts if part != "--default"]
        return (" ".join(target_parts).strip(), persist_default)

    @staticmethod
    def _persist_default_skill(qualified_id: str) -> None:
        from agentnexus.core.config import load_config_yaml, write_config_yaml

        data = load_config_yaml()
        data["default_skill"] = qualified_id
        write_config_yaml(data)

    @staticmethod
    def _clear_default_skill() -> None:
        from agentnexus.core.config import load_config_yaml, write_config_yaml

        data = load_config_yaml()
        data.pop("default_skill", None)
        write_config_yaml(data)

    @staticmethod
    def _format_skill_status(
        registry: SkillRegistry,
        current: SkillEntry | None = None,
        status: str = "idle",
        applied: bool = False,
        default_skill: str = "",
        runtime=None,
    ) -> str:
        current_id = current.qualified_id if current else "default/default"
        default_id = default_skill or "default/default"
        roots = ", ".join(str(root) for root in registry.roots) or "-"
        policy = format_tool_policy_summary(current.workflow.tool_policy if current else None)
        lines = [
            "[bold]Skill 状态[/]",
            f"[dim]current:[/] {current_id}",
            f"[dim]default:[/] {default_id}",
            f"[dim]status:[/] {status}",
            f"[dim]applied:[/] {applied}",
            f"[dim]tools:[/] {policy}",
            f"[dim]auto_route:[/] {getattr(runtime, 'auto_route_enabled', True) if runtime is not None else True}",
            f"[dim]available:[/] {len(registry.list())}",
            f"[dim]roots:[/] {roots}",
        ]
        if runtime is not None and getattr(runtime, "auto_route_reason", ""):
            source = getattr(runtime, "auto_route_source", "") or "auto"
            lines.append(f"[dim]auto_selected:[/] {source}: {runtime.auto_route_reason}")
        if runtime is not None and getattr(runtime, "last_run_status", ""):
            lines.extend([
                f"[dim]last_run:[/] {runtime.last_run_status} {runtime.last_run_id}",
                f"[dim]steps:[/] {runtime.ok_steps}/{runtime.step_count} ok  {runtime.error_steps} error",
            ])
        has_resources = (
            runtime is not None
            and (
                getattr(runtime, "scripts", 0)
                or getattr(runtime, "references", 0)
                or getattr(runtime, "assets", 0)
            )
        )
        if has_resources:
            lines.append(
                f"[dim]resources:[/] scripts={runtime.scripts} references={runtime.references} assets={runtime.assets}"
            )
        if registry.errors:
            lines.append(f"[#e06c75]errors:[/] {len(registry.errors)}")
        return "\n".join(lines)

    @staticmethod
    def _format_skill_list(registry: SkillRegistry) -> str:
        entries = registry.list()
        if not entries:
            if registry.errors:
                return "[dim]未发现可用 skills。[/]\n" + "\n".join(
                    f"[#e06c75]- {err}[/]" for err in registry.errors[:5]
                )
            return "[dim]未发现可用 skills。[/]"
        lines = ["[bold]Skills[/]"]
        for entry in entries:
            desc = f" [dim]- {entry.description[:60]}[/]" if entry.description else ""
            source = "workflow" if entry.source_kind == "workflow" else "skill"
            resources = ChatScreen._format_skill_resource_counts(entry)
            resource_text = f" [dim]{resources}[/]" if resources else ""
            lines.append(
                f"- [#6ba5f2]{entry.qualified_id}[/] {entry.display_name} "
                f"[dim]({source})[/]{resource_text}{desc}"
            )
        if registry.errors:
            lines.append(f"[#e06c75]{len(registry.errors)} 个 skill 加载失败，见 /skill status[/]")
        return "\n".join(lines)

    @staticmethod
    def _format_skill_resource_counts(entry: SkillEntry) -> str:
        counts = {"script": 0, "reference": 0, "asset": 0}
        for resource in getattr(entry.workflow, "resources", []) or []:
            counts[resource.type] = counts.get(resource.type, 0) + 1
        parts = []
        if counts["script"]:
            parts.append(f"scripts={counts['script']}")
        if counts["reference"]:
            parts.append(f"refs={counts['reference']}")
        if counts["asset"]:
            parts.append(f"assets={counts['asset']}")
        return " ".join(parts)

    @staticmethod
    def _format_skill_validation(registry: SkillRegistry, errors: list[str], target: str | None = None) -> str:
        scope = target or "all"
        if not errors:
            return f"[green]Skill validation passed[/] [dim]{scope}; {len(registry.list())} skills[/]"
        lines = [f"[#e06c75]Skill validation failed[/] [dim]{scope}; {len(errors)} errors[/]"]
        lines.extend(f"- {error}" for error in errors[:8])
        if len(errors) > 8:
            lines.append(f"[dim]... {len(errors) - 8} more[/]")
        return "\n".join(lines)

    @staticmethod
    def _format_skill_errors(registry: SkillRegistry) -> str:
        lines = [f"[#e06c75]Skill registry 发现 {len(registry.errors)} 个加载错误[/]"]
        lines.extend(f"[dim]- {err}[/]" for err in registry.errors[:3])
        if len(registry.errors) > 3:
            lines.append("[dim]更多错误可通过 /skill list 查看。[/]")
        return "\n".join(lines)

    def _refresh_skill_panel(self):
        try:
            self._sync_skill_service_state()
            runtime = self._skill_runtime_summary()
            available = self._available_skill_summary()
            if self._current_skill is None:
                if runtime:
                    self._side_panel.update_skill(
                        "default", "default", self._skill_status, runtime=runtime, available=available
                    )
                else:
                    self._side_panel.update_skill("default", "default", self._skill_status, available=available)
            else:
                if runtime:
                    self._side_panel.update_skill(
                        self._current_skill.namespace,
                        self._current_skill.workflow_id,
                        self._skill_status,
                        runtime=runtime,
                        available=available,
                    )
                else:
                    self._side_panel.update_skill(
                        self._current_skill.namespace,
                        self._current_skill.workflow_id,
                        self._skill_status,
                        available=available,
                    )
        except Exception as e:
            logger.debug("Failed to refresh skill panel: %s", e)

    def _sync_skill_service_state(self):
        if self._skill_service is None:
            return
        try:
            snapshot = self._skill_service.snapshot()
            self._skill_status = snapshot.status
            self._current_skill = getattr(self._skill_service, "current", None)
        except Exception as e:
            logger.debug("Failed to sync skill service state: %s", e)

    def _skill_runtime_summary(self) -> dict:
        if self._skill_service is None:
            return {}
        try:
            snapshot = self._skill_service.snapshot()
        except Exception as e:
            logger.debug("Failed to get skill runtime summary: %s", e)
            return {}
        status = getattr(snapshot, "last_run_status", "")
        if not isinstance(status, str) or not status:
            return {}
        return {
            "status": status,
            "steps": getattr(snapshot, "step_count", 0),
            "ok": getattr(snapshot, "ok_steps", 0),
            "errors": getattr(snapshot, "error_steps", 0),
            "skipped": getattr(snapshot, "skipped_steps", 0),
            "scripts": getattr(snapshot, "scripts", 0),
            "references": getattr(snapshot, "references", 0),
            "assets": getattr(snapshot, "assets", 0),
            "auto_reason": getattr(snapshot, "auto_route_reason", ""),
            "auto_source": getattr(snapshot, "auto_route_source", ""),
        }

    def _available_skill_summary(self) -> list[tuple[str, str, str]]:
        if self._skill_service is not None:
            try:
                return list(self._skill_service.snapshot().available_skills)
            except Exception as e:
                logger.debug("Failed to get available skill summary: %s", e)
                return []
        registry = self._skill_registry
        if registry is None:
            return []
        return [
            (entry.qualified_id, entry.display_name, entry.description)
            for entry in registry.list()
            if entry.source_kind == "skill"
        ]

    def _handle_dynamic_skill_command(self, cmd: str, arg: str, short_form: bool = False) -> bool:
        if not cmd.startswith("/"):
            return False
        if short_form:
            target = cmd[1:].strip()
        else:
            if not cmd.endswith("-skill"):
                return False
            target = cmd[1:-6].strip()
        if not target:
            return False
        entry = self._resolve_dynamic_skill(target)
        instruction = arg.strip()
        if not instruction:
            command = f"/{target}" if short_form else f"/{target}-skill"
            self._chat_area.add_system(f"[dim]用法: {command} <指令>[/]")
            return True
        if entry is None:
            return False
        sid = self._chat_session.id
        submission = {"text": instruction, "skill_id": entry.qualified_id, "attachments": self._attachments.pop(sid, None)}
        if self._skill_service is None:
            profile = entry.workflow.to_session_profile()
            try:
                validate_session_profile(profile)
            except Exception as exc:
                self._skill_status = "error"
                self._refresh_skill_panel()
                self._chat_area.add_system(f"[dim]Skill 命令失败: {exc}[/]")
                return True
            submission["session_profile"] = profile
        self._chat_area.add_system(f"[green]Skill 指令已提交[/] {entry.qualified_id}")
        self._submit_session_message(submission, sid)
        return True

    def _resolve_dynamic_skill(self, target: str) -> SkillEntry | None:
        if self._skill_registry is None:
            self._init_skill_registry()
        registry = self._skill_registry
        if registry is None:
            return None
        candidates = [target]
        if "/" not in target:
            candidates.append(target.replace("-", "_"))
        for candidate in candidates:
            try:
                entry = registry.get(candidate)
            except ValueError:
                continue
            if entry is not None:
                return entry
        for entry in registry.list():
            if entry.workflow_id == target or entry.workflow_id.replace("_", "-") == target:
                return entry
            if entry.qualified_id.replace("/", "-") == target:
                return entry
        return None

    def _handle_mcp_command(self, arg: str):
        if self._mcp_manager is None:
            self._chat_area.add_system("[dim]当前会话未启用 MCP。[/]")
            self._refresh_mcp_panel()
            return

        try:
            self._refresh_mcp_panel()
            parts = arg.strip().split()
            subcmd = parts[0] if parts else "status"
            rest = parts[1:]

            if subcmd == "status":
                self._chat_area.add_system(self._format_mcp_status(self._mcp_manager.status_snapshot()))
            elif subcmd == "tools":
                server_name = rest[0] if rest else None
                self._chat_area.add_system(
                    self._format_mcp_tools(self._mcp_manager.status_snapshot(), server_name=server_name)
                )
            elif subcmd == "resources":
                server_name = rest[0] if rest else None
                self._chat_area.add_system(
                    self._format_mcp_resources(self._mcp_manager.status_snapshot(), server_name=server_name)
                )
            elif subcmd == "prompts":
                server_name = rest[0] if rest else None
                self._chat_area.add_system(
                    self._format_mcp_prompts(self._mcp_manager.status_snapshot(), server_name=server_name)
                )
            elif subcmd == "failures":
                self._chat_area.add_system(self._format_mcp_failures(self._mcp_manager.status_snapshot()))
            elif subcmd == "retry":
                server_name = None
                if rest and rest[0] != "--failed":
                    server_name = rest[0]
                result = self._mcp_manager.retry_failed(server_name=server_name)
                if result.get("reconnected") and getattr(self._agent, "tool_executor", None) is not None:
                    self._mcp_manager.register_tools(self._agent.tool_executor)
                    if hasattr(self._agent, "set_mcp_context"):
                        self._agent.set_mcp_context(self._mcp_manager.auto_context())
                self._chat_area.add_system(self._format_mcp_retry_result(result))
            elif subcmd in {"enable", "disable", "reload"}:
                server_name = rest[0] if rest else None
                if self._capability_runtime is not None:
                    if subcmd == "enable":
                        result = self._capability_runtime.enable("mcp", server_name)
                    elif subcmd == "disable":
                        result = self._capability_runtime.disable("mcp", server_name)
                    else:
                        result = self._capability_runtime.reload("mcp")
                    self._chat_area.add_system(f"[dim]MCP {subcmd}: {server_name or 'all'} - {result.get('mcp')}[/]")
                else:
                    if not server_name and subcmd != "reload":
                        self._chat_area.add_system("[dim]用法: /mcp enable|disable <server>[/]")
                        return
                    if subcmd == "enable":
                        result = self._mcp_manager.enable_server(server_name)
                    elif subcmd == "disable":
                        result = self._mcp_manager.disable_server(server_name)
                    else:
                        result = self._mcp_manager.reload_server(server_name)
                    if getattr(self._agent, "tool_executor", None) is not None:
                        self._agent.tool_executor.unregister_source_prefix("mcp:", source_type="mcp")
                        self._mcp_manager.register_tools(self._agent.tool_executor)
                    if hasattr(self._agent, "set_mcp_context"):
                        self._agent.set_mcp_context(self._mcp_manager.auto_context())
                    self._chat_area.add_system(f"[dim]MCP {subcmd}: {result}[/]")
                self._refresh_mcp_panel()
                self._refresh_tools_panel()
            else:
                self._chat_area.add_system(
                    "[dim]用法: /mcp [status|tools [server]|resources [server]|"
                    "prompts [server]|failures|retry [server|--failed]][/]"
                )
        except Exception as exc:
            self._chat_area.add_system(f"[dim]MCP 命令失败: {exc}[/]")
            try:
                self._side_panel.add_timeline_event("error", f"MCP command failed: {exc}")
            except Exception as e:
                logger.debug("Failed to add MCP error timeline event: %s", e)

    def _refresh_mcp_panel(self):
        try:
            snapshot = self._mcp_manager.status_snapshot() if self._mcp_manager is not None else None
            self._side_panel.update_mcp(snapshot)
        except Exception as e:
            logger.debug("Failed to refresh MCP panel: %s", e)

    def _refresh_tools_panel(self):
        try:
            registry = getattr(self._agent, "tool_executor", None)
            tools = []
            if registry is not None:
                for meta in registry.list_tools_with_meta():
                    risk = getattr(meta.risk_level, "value", str(meta.risk_level))
                    tools.append({"name": meta.name, "risk": risk})
            self._side_panel.update_tools(tools)
        except Exception as e:
            logger.debug("Failed to refresh tools panel: %s", e)

    def _refresh_model_panel(self, strategy: str = ""):
        if not hasattr(self, '_side_panel') or not self._agent:
            return
        try:
            model = getattr(self._agent, "model_id", "unknown") if self._agent else "unknown"
            ctx = "?"
            caps = getattr(getattr(self._agent, "llm_client", None), "capabilities", None)
            if caps and hasattr(caps, "max_context_tokens"):
                ctx = f"{caps.max_context_tokens // 1000}k" if caps.max_context_tokens else "?"
            self._side_panel.update_model(model, ctx, strategy)
            if hasattr(self, "_hud"):
                llm = getattr(self._agent, "llm_client", None)
                base_url = getattr(llm, "base_url", "")
                if isinstance(model, str) and isinstance(base_url, str):
                    self._hud.update_model(model, base_url, workspace=self._chat_session.workspace)
        except Exception as e:
            logger.debug("Failed to refresh model panel: %s", e)

    def _refresh_todo_panel(self):
        if not hasattr(self, '_side_panel') or not self._agent:
            return
        try:
            todo_list = getattr(self._agent, '_todo_list', None)
            if todo_list is None:
                self._side_panel.update_todo([])
                return
            items = todo_list.list_items()
            self._side_panel.update_todo([
                {"id": t.id, "description": t.description, "status": t.status}
                for t in items
            ])
        except Exception as e:
            logger.debug("Failed to refresh todo panel: %s", e)

    def _record_turn_summary(self, question: str, answer: str = ""):
        try:
            tools = ", ".join(dict.fromkeys(self._turn_tool_names)) or "no tools"
            thought_part = f"{self._turn_thought_count} thoughts"
            answer_part = collapse_and_truncate(answer, 32) if answer else "no answer"
            question_part = collapse_and_truncate(question, 24)
            self._side_panel.add_timeline_event(
                "summary",
                f"{question_part}: {tools}; {thought_part}; {answer_part}",
            )
        except Exception as e:
            logger.debug("Failed to record turn summary: %s", e)
        finally:
            self._turn_tool_names = []
            self._turn_thought_count = 0

    def _apply_workflow_event(self, event):
        try:
            marker = "error" if getattr(event, "status", "") == "error" else "run"
            text = f"{event.step_type}:{event.step_id} {event.status}"
            summary = getattr(event, "summary", "")
            if summary:
                text = f"{text} - {summary}"
            self._side_panel.add_timeline_event(marker, collapse_and_truncate(text, 80))
        except Exception as e:
            logger.debug("Failed to apply workflow event: %s", e)

    def _prepare_agent_question(self, text: str) -> str:
        if self._current_skill is None and self._skill_service is None:
            return text
        if self._skill_service is not None:
            if hasattr(self._agent, "set_available_skill_context"):
                try:
                    recommendations = self._skill_service.get_recommendations(text) or None
                except Exception as rec_exc:
                    logger.debug("Skill recommendations failed: %s", rec_exc)
                    recommendations = None
                self._agent.set_available_skill_context(
                    self._skill_service.available_skill_context(recommendations=recommendations)
                )
            workflow_result = self._skill_service.prepare_message(
                text,
                tool_executor=getattr(self._agent, "tool_executor", None),
                memory_manager=self._memory,
            )
        else:
            from agentnexus.skills import WorkflowRuntime

            profile = self._current_skill.workflow.to_session_profile()
            workflow_result = WorkflowRuntime().prepare(
                text,
                profile,
                tool_executor=getattr(self._agent, "tool_executor", None),
                memory_manager=self._memory,
            )
        for workflow_event in workflow_result.events:
            self._chat_service.record_workflow_event(self._current_run_id, workflow_event)
            try:
                self.app.call_from_thread(self._apply_workflow_event, workflow_event)
            except Exception as e:
                logger.debug("call_from_thread failed for workflow event, applying directly: %s", e)
                self._apply_workflow_event(workflow_event)
        if self._skill_service is not None:
            try:
                snapshot = self._skill_service.snapshot()
                if snapshot.auto_route_reason:
                    source = snapshot.auto_route_source or "auto"
                    self._side_panel.add_timeline_event(
                        "run",
                        collapse_and_truncate(
                            f"Auto skill ({source}): {snapshot.current} - {snapshot.auto_route_reason}",
                            96,
                        ),
                    )
            except Exception as e:
                logger.debug("Failed to display auto-route snapshot: %s", e)
        self._refresh_skill_panel()
        workflow_ctx = getattr(workflow_result, "workflow_context", None)
        if workflow_ctx and hasattr(self._agent, "set_workflow_context"):
            self._agent.set_workflow_context(workflow_ctx)
        return workflow_result.enhanced_question

    def _stop_spinner(self):
        if self._spinner_timer is not None:
            self._spinner_timer.stop()
            self._spinner_timer = None
        self._spinner_frames = None

    def _force_interrupt_agent(self):
        if self._current_run_id:
            self._chat_service.cancel_run(self._current_run_id, reason="用户请求停止")
        self._stop_spinner()
        self._current_tool_widget = None
        try:
            snapshot = self._chat_service.get_run_snapshot(self._current_run_id) if self._current_run_id else None
            answer = snapshot.answer if snapshot is not None else ""
            question = snapshot.question if snapshot is not None else "interrupted turn"
            self._chat_area.add_message("system", "已请求停止当前任务，等待执行结束。")
            self._side_panel.add_timeline_event("error", "用户请求停止")
            self._record_turn_summary(question, answer)
        except Exception as e:
            logger.debug("Failed to record interrupt summary: %s", e)

    def _tick_spinner(self):
        if not self._current_tool_widget or not self._spinner_frames:
            return
        frame = next(self._spinner_frames)
        label = self._current_tool_widget.query_one("#tool-name", Label)
        label.update(f"{frame} {self._current_tool_widget.tool_name}")

    @staticmethod
    def _format_mcp_status(snapshot: dict) -> str:
        servers = snapshot.get("servers", []) or []
        lines = [
            "[bold]MCP 状态[/]",
            f"[dim]started:[/] {snapshot.get('started', False)}",
            f"[dim]servers:[/] {snapshot.get('connected_count', 0)}/{snapshot.get('server_count', 0)} 已连接",
            f"[dim]tools:[/] {snapshot.get('tool_count', 0)}",
            f"[dim]resources:[/] {snapshot.get('resource_count', 0)} "
            f"(+templates {snapshot.get('resource_template_count', 0)})",
            f"[dim]prompts:[/] {snapshot.get('prompt_count', 0)}",
            f"[dim]failures:[/] {snapshot.get('failure_count', 0)}",
        ]
        if servers:
            lines.append("[bold]Servers:[/]")
            for server in servers:
                raw_state = server.get("state") or ("healthy" if server.get("connected") else "disconnected")
                state = "[green]healthy[/]" if raw_state == "healthy" else f"[dim]{raw_state}[/]"
                last_ping = server.get("last_ping_at")
                ping_text = f" ping={int(last_ping)}" if last_ping else ""
                lines.append(
                    f"  - {server.get('name', 'unknown')} ({server.get('transport', 'unknown')}) {state} "
                    f"tools={len(server.get('tool_names', []) or [])} "
                    f"resources={server.get('resource_count', 0)} prompts={server.get('prompt_count', 0)} "
                    f"reconnects={server.get('reconnect_attempts', 0)}{ping_text}"
                )
        return "\n".join(lines)

    @staticmethod
    def _format_mcp_tools(snapshot: dict, server_name: str | None = None) -> str:
        servers = snapshot.get("servers", []) or []
        lines = ["[bold]MCP Tools[/]"]
        matched = False
        for server in servers:
            if server_name and server.get("name") != server_name:
                continue
            matched = True
            tools = server.get("tool_names", []) or []
            lines.append(f"- {server.get('name', 'unknown')}: {', '.join(tools) if tools else '[dim]无工具[/]'}")
        if server_name and not matched:
            return f"[dim]未找到 MCP server: {server_name}[/]"
        if not matched:
            return "[dim]当前没有已导入的 MCP 工具。[/]"
        return "\n".join(lines)

    @staticmethod
    def _format_mcp_resources(snapshot: dict, server_name: str | None = None) -> str:
        servers = snapshot.get("servers", []) or []
        lines = ["[bold]MCP Resources[/]"]
        matched = False
        for server in servers:
            if server_name and server.get("name") != server_name:
                continue
            matched = True
            tools = server.get("resource_tool_names", []) or []
            count = server.get("resource_count", 0)
            templates = server.get("resource_template_count", 0)
            lines.append(
                f"- {server.get('name', 'unknown')}: resources={count} templates={templates} "
                f"tools={', '.join(tools) if tools else '[dim]无资源工具[/]'}"
            )
        if server_name and not matched:
            return f"[dim]未找到 MCP server: {server_name}[/]"
        if not matched:
            return "[dim]当前没有已导入的 MCP resources。[/]"
        return "\n".join(lines)

    @staticmethod
    def _format_mcp_prompts(snapshot: dict, server_name: str | None = None) -> str:
        servers = snapshot.get("servers", []) or []
        lines = ["[bold]MCP Prompts[/]"]
        matched = False
        for server in servers:
            if server_name and server.get("name") != server_name:
                continue
            matched = True
            tools = server.get("prompt_tool_names", []) or []
            count = server.get("prompt_count", 0)
            lines.append(
                f"- {server.get('name', 'unknown')}: prompts={count} "
                f"tools={', '.join(tools) if tools else '[dim]无 prompt 工具[/]'}"
            )
        if server_name and not matched:
            return f"[dim]未找到 MCP server: {server_name}[/]"
        if not matched:
            return "[dim]当前没有已导入的 MCP prompts。[/]"
        return "\n".join(lines)

    @staticmethod
    def _format_mcp_failures(snapshot: dict) -> str:
        servers = snapshot.get("servers", []) or []
        failed = [server for server in servers if server.get("failure")]
        if not failed:
            return "[dim]当前没有 MCP 失败项。[/]"
        lines = ["[bold]MCP Failures[/]"]
        for server in failed:
            lines.append(f"- {server.get('name', 'unknown')}: {server.get('failure')}")
        return "\n".join(lines)

    @staticmethod
    def _format_mcp_retry_result(result: dict) -> str:
        retried = result.get("retried", []) or []
        reconnected = result.get("reconnected", []) or []
        failed = result.get("failed", {}) or {}
        skipped = result.get("skipped", []) or []
        lines = ["[bold]MCP Retry[/]"]
        lines.append(f"retried: {', '.join(retried) if retried else '-'}")
        lines.append(f"reconnected: {', '.join(reconnected) if reconnected else '-'}")
        lines.append(f"skipped: {', '.join(skipped) if skipped else '-'}")
        if failed:
            for name, reason in failed.items():
                lines.append(f"failed: {name} -> {reason}")
        return "\n".join(lines)

    def _on_compact_event(self, event: dict, *, session_id=None):
        """Called from agent thread — forward to main thread for UI update."""
        self.app.call_from_thread(self._apply_compact_event, event, session_id)

    def _apply_compact_event(self, event: dict, session_id=None):
        session_id = session_id or self._chat_session.id
        e = event["event"]
        if e == "start":
            self._compacting_sessions.add(session_id)
        else:
            self._compacting_sessions.discard(session_id)
        if session_id != self._chat_session.id:
            return
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


    def _drain_message_queue(self) -> None:
        """Process the next queued message if available."""
        if self._chat_service.queue_size > 0:
            next_item = self._chat_service.dequeue_message()
            if next_item is not None:
                _session_id, text = next_item
                self._chat_area.add_system("[dim]处理排队消息...[/]")
                self._chat_service.mark_processing(True)
                self.set_timer(0, lambda: self._run_agent(text))


def _format_ctx_window(tokens) -> str:
    if not isinstance(tokens, int) or tokens <= 0:
        return "unknown"
    if tokens >= 1_000_000:
        return f"{tokens / 1_000_000:.1f}m"
    return f"{tokens // 1000}k"
