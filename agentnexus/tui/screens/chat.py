"""ChatScreen — main chat interface with real ReActAgent backend."""

import asyncio
import threading
import time
from itertools import cycle

from rich.markdown import Markdown
from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.events import Key
from textual.screen import Screen
from textual.widget import Widget
from textual.widgets import Input, Label, Static
from textual.worker import get_current_worker

from agentnexus.core.config import get_settings
from agentnexus.core.text_utils import collapse_and_truncate
from agentnexus.memory.short_term import ShortTermMemory
from agentnexus.observability.tracer import trace_manager
from agentnexus.services.chat import ChatService
from agentnexus.skills import (
    SkillEntry,
    SkillRegistry,
    format_tool_policy_summary,
    validate_session_profile,
)
from agentnexus.tools.result_format import (
    condense_file_result,
    condense_search_result,
    extract_diff_parts,
    format_subagent_result,
    summarize_tool_result,
)
from agentnexus.tui.widgets.confirm_dialog import ConfirmDialog
from agentnexus.tui.widgets.hud import HUD
from agentnexus.tui.widgets.input_bar import InputBar
from agentnexus.tui.widgets.message import ChatMessage, ToolCall, render_diff_with_colors
from agentnexus.tui.widgets.side_panel import SidePanel

COMMAND_DEFINITIONS: tuple[tuple[str, str], ...] = (
    ("/help", "显示命令帮助"),
    ("/clear", "清屏；--all 同时清除检查点"),
    ("/undo", "回退到上一个检查点"),
    ("/redo", "重做到下一个检查点"),
    ("/log", "查看检查点日志"),
    ("/status", "查看状态"),
    ("/compact", "压缩当前上下文"),
    ("/stats", "查看运行统计"),
    ("/skill", "管理 Skill"),
    ("/mcp", "管理 MCP server"),
    ("/plugin", "管理插件"),
    ("/exit", "退出 TUI"),
)

COMMAND_SUBCOMMANDS: dict[str, tuple[str, ...]] = {
    "/skill": ("status", "list", "use", "enable", "disable", "default", "validate", "reset"),
    "/mcp": ("status", "tools", "resources", "prompts", "failures", "retry", "enable", "disable", "reload"),
    "/plugin": ("status", "list", "enable", "disable", "reload"),
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
    ]

    ESC_DOUBLE_TAP_SECONDS = 0.6

    def __init__(self, agent, memory, version, mcp_manager=None, skill_service=None, capability_runtime=None):
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
        self._turn_tool_names: list[str] = []
        self._turn_thought_count = 0
        self._skill_registry: SkillRegistry | None = None
        self._current_skill: SkillEntry | None = None
        self._skill_status = "idle"
        self._last_escape_at = 0.0
        self._agent_worker = None
        self._streaming_msg_widget = None
        self._streaming_buffer = ""
        self._last_stream_update = 0.0
        self._chat_service = ChatService(
            agent=agent,
            memory_manager=memory,
            version_manager=version,
            skill_service=skill_service,
            tool_executor=getattr(agent, "tool_executor", None),
            capability_runtime=capability_runtime,
        )
        self._chat_session = self._chat_service.start_session()
        self._chat_service.mark_processing(False)
        self._current_run_id: str = ""
        self._current_turn = None
        # Hook compact events for TUI visibility
        if self._memory:
            self._memory._on_compact = self._on_compact_event

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
        self._chat_input = InputBar(id="input-area")
        yield self._chat_input
        self._hud = HUD(id="hud")
        yield self._hud

    def _render_top_bar(self) -> str:
        model = getattr(self._agent, "model_id", "v4-flash") if self._agent else "v4-flash"
        branch = self._version.status().get("branch", "main") if self._version else "main"

        left = "[#6ba5f2]AgentNexus[/] [dim]workspace[/]"
        center = f"[dim]branch[/] {branch}  [dim]model[/] [#6ba5f2]{model}[/]"
        right = "[dim]^H help  ^L clear  Esc focus[/]"

        return f"{left}  {center}  {right}"

    def on_mount(self):
        self._chat_area.add_system("[#6ba5f2]AgentNexus ready[/] [dim]Ask a question or type /help.[/]")
        self._render_restored_history()
        self._init_skill_registry()
        self._refresh_version_display()
        if hasattr(self, "_side_panel"):
            self._refresh_skill_panel()
            self._refresh_model_panel()
            self._refresh_tools_panel()
            self._refresh_mcp_panel()
        if hasattr(self, '_hud') and self._agent:
            try:
                caps = self._agent.llm_client.capabilities
                strategy = "原生工具" if caps.supports_tool_calling else "JSON模式"
                self._hud.update_capabilities(
                    supports_thinking=caps.supports_thinking,
                    strategy=strategy,
                )
            except Exception:
                pass
        self.call_after_refresh(lambda: self.query_one("#chat-input", Input).focus())

    def _render_restored_history(self):
        if not self._memory or not getattr(self._memory, "short_term", None):
            return
        try:
            messages = self._memory.short_term.get_all()
        except Exception:
            return
        visible = [m for m in messages if m.get("role") in {"system", "user", "assistant"}]
        if not visible:
            return
        self._chat_area.add_system(f"[dim]Restored {len(visible)} messages from this session.[/]")
        for msg in visible:
            role = msg.get("role", "")
            content = str(msg.get("content", "") or "")
            if not content:
                continue
            if role == "system":
                self._chat_area.add_system(content)
            else:
                self._chat_area.add_message(role, content)

    # ── custom submit ──────────────────────────────────────────

    def on_input_bar_app_submit(self, event: InputBar.AppSubmit):
        text = event.text
        inp = self.query_one("#chat-input", Input)
        inp.value = ""
        self._update_command_suggestions("")
        self._chat_area.add_message("user", text)
        if text.startswith("/"):
            self._handle_command(text)
        elif self._chat_service.is_processing:
            # Agent is busy — queue the message
            self._chat_service.enqueue_message(self._chat_session.id, text)
        else:
            self._chat_service.mark_processing(True)
            self._run_agent(text)

    def on_input_bar_app_input_changed(self, event: InputBar.AppInputChanged):
        self._update_command_suggestions(event.text)

    def on_key(self, event: Key):
        if event.key != "escape":
            return
        now = time.monotonic()
        if now - self._last_escape_at <= self.ESC_DOUBLE_TAP_SECONDS:
            event.stop()
            self._last_escape_at = 0.0
            self._force_interrupt_agent()
            return
        self._last_escape_at = now
        event.stop()
        self.action_focus_input()

    # ── keybindings ────────────────────────────────────────────

    def action_clear_screen(self):
        self._chat_area.clear_all()

    def action_show_help(self):
        commands = "  ".join(command for command, _ in COMMAND_DEFINITIONS)
        self._chat_area.add_system(
            f"[dim]命令:[/] {commands}\n"
            "       /skill [status|list|use <id> [--default]|enable <id>|disable <id>|"
            "default <id>|validate [id]|reset]\n"
            "       /mcp [status|tools|resources|prompts|failures|retry|enable|disable|reload]\n"
            "       /plugin [status|list|enable|disable|reload]"
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
        except Exception:
            pass
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
        elif cmd == "/exit":
            self.app.exit()
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
        elif cmd == "/compact" and self._memory:
            tokens_before = self._memory.estimate_stm_tokens()
            saved = self._memory.maybe_compact(custom_instructions=arg, is_auto=False)
            if saved > 0:
                self._chat_area.add_system(
                    f"[dim]已压缩对话上下文 (释放 {saved} tokens, 压缩前 {tokens_before} tokens)[/]"
                )
            else:
                self._chat_area.add_system("[dim]当前上下文未达到压缩阈值[/]")
        elif cmd == "/stats":
            self._chat_area.add_system(self._hud._build_text())
        elif cmd == "/mcp":
            self._handle_mcp_command(arg)
        elif cmd == "/plugin":
            self._handle_plugin_command(arg)
        elif cmd == "/skill":
            self._handle_skill_command(arg)
        else:
            if self._handle_dynamic_skill_command(cmd, arg, short_form=True):
                return
            if self._handle_dynamic_skill_command(cmd, arg):
                return
            self._chat_area.add_system(f"[dim]未知: {cmd}[/]")

    def _update_command_suggestions(self, text: str):
        suggestions = self._match_command_suggestions(text)
        try:
            palette = self.query_one("#command-palette", VerticalScroll)
            self.query_one("#command-palette-content", Static).update(suggestions)
            palette.styles.display = "block" if suggestions else "none"
            if suggestions:
                palette.scroll_home(animate=False)
        except Exception:
            pass

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
            except Exception:
                pass
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
            except Exception:
                pass

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
        except Exception:
            pass

    def _sync_skill_service_state(self):
        if self._skill_service is None:
            return
        try:
            snapshot = self._skill_service.snapshot()
            self._skill_status = snapshot.status
            self._current_skill = getattr(self._skill_service, "current", None)
        except Exception:
            pass

    def _skill_runtime_summary(self) -> dict:
        if self._skill_service is None:
            return {}
        try:
            snapshot = self._skill_service.snapshot()
        except Exception:
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
            except Exception:
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
        if self._skill_service is not None:
            try:
                self._skill_service.use(entry.qualified_id)
                self._skill_status = self._skill_service.snapshot().status
                self._current_skill = entry
            except Exception as exc:
                self._skill_status = "error"
                self._refresh_skill_panel()
                self._chat_area.add_system(f"[dim]Skill 命令失败: {exc}[/]")
                return True
        else:
            profile = entry.workflow.to_session_profile()
            try:
                validate_session_profile(profile)
                if hasattr(self._agent, "set_session_profile"):
                    self._agent.set_session_profile(profile)
            except Exception as exc:
                self._skill_status = "error"
                self._refresh_skill_panel()
                self._chat_area.add_system(f"[dim]Skill 命令失败: {exc}[/]")
                return True
            self._current_skill = entry
            self._skill_status = "selected"
        self._refresh_skill_panel()
        self._chat_area.add_system(f"[green]已使用 skill[/] {entry.qualified_id} [dim]执行指令。[/]")
        self._chat_service.mark_processing(True)
        self._run_agent(instruction)
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
            except Exception:
                pass

    def _refresh_mcp_panel(self):
        try:
            snapshot = self._mcp_manager.status_snapshot() if self._mcp_manager is not None else None
            self._side_panel.update_mcp(snapshot)
        except Exception:
            pass

    def _handle_plugin_command(self, arg: str):
        if self._capability_runtime is None:
            self._chat_area.add_system("[dim]Plugin runtime unavailable.[/]")
            return
        parts = arg.strip().split()
        subcmd = parts[0] if parts else "status"
        name = parts[1] if len(parts) > 1 else None
        try:
            if subcmd in {"status", "list"}:
                status = self._capability_runtime.extension_manager.status()
                lines = ["[bold]Plugins[/]"]
                enabled = self._capability_runtime.snapshot().plugin_enabled
                for descriptor in status.discovered:
                    state = "enabled" if enabled.get(descriptor.name, False) else "disabled"
                    errors = f" errors={len(descriptor.errors)}" if descriptor.errors else ""
                    lines.append(f"- {descriptor.name}: {state}{errors}")
                if not status.discovered:
                    lines.append("[dim]No plugins discovered.[/]")
                self._chat_area.add_system("\n".join(lines))
            elif subcmd == "enable" and name:
                result = self._capability_runtime.enable("plugins", name)
                self._chat_area.add_system(f"[dim]Plugin enabled: {name} - {result.get('plugins')}[/]")
                self._refresh_tools_panel()
            elif subcmd == "disable" and name:
                result = self._capability_runtime.disable("plugins", name)
                self._chat_area.add_system(f"[dim]Plugin disabled: {name} - {result.get('plugins')}[/]")
                self._refresh_tools_panel()
            elif subcmd == "reload":
                result = self._capability_runtime.reload("plugins")
                self._chat_area.add_system(f"[dim]Plugins reloaded: {result.get('plugins')}[/]")
                self._refresh_tools_panel()
            else:
                self._chat_area.add_system("[dim]用法: /plugin [status|list|enable <name>|disable <name>|reload][/]")
        except Exception as exc:
            self._chat_area.add_system(f"[dim]Plugin command failed: {exc}[/]")

    def _refresh_tools_panel(self):
        try:
            registry = getattr(self._agent, "tool_executor", None)
            tools = []
            if registry is not None:
                for meta in registry.list_tools_with_meta():
                    risk = getattr(meta.risk_level, "value", str(meta.risk_level))
                    tools.append({"name": meta.name, "risk": risk})
            self._side_panel.update_tools(tools)
        except Exception:
            pass

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
        except Exception:
            pass

    def _refresh_todo_panel(self):
        if not hasattr(self, '_side_panel') or not self._agent:
            return
        try:
            todo_list = getattr(self._agent, '_todo_list', None)
            if todo_list is None:
                return
            items = todo_list.list_items()
            self._side_panel.update_todo([
                {"id": t.id, "description": t.description, "status": t.status}
                for t in items
            ])
        except Exception:
            pass

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
        except Exception:
            pass
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
        except Exception:
            pass

    def _prepare_agent_question(self, text: str) -> str:
        if self._current_skill is None and self._skill_service is None:
            return text
        if self._skill_service is not None:
            if hasattr(self._agent, "set_available_skill_context"):
                self._agent.set_available_skill_context(self._skill_service.available_skill_context())
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
            except Exception:
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
            except Exception:
                pass
        self._refresh_skill_panel()
        return workflow_result.enhanced_question

    # ── spinner animation ────────────────────────────────────

    def _stop_spinner(self):
        if self._spinner_timer is not None:
            self._spinner_timer.stop()
            self._spinner_timer = None
        self._spinner_frames = None

    def _force_interrupt_agent(self):
        if self._current_run_id:
            self._chat_service.cancel_run(self._current_run_id, reason="用户双击 ESC 强制中断")
        worker = getattr(self, "_agent_worker", None)
        if worker is not None:
            try:
                worker.cancel()
            except Exception:
                pass
        self._chat_service.mark_processing(False)
        self._stop_spinner()
        self._current_tool_widget = None
        try:
            self._chat_area.query_one("#loading-indicator").remove()
        except Exception:
            pass
        try:
            snapshot = self._chat_service.get_run_snapshot(self._current_run_id) if self._current_run_id else None
            answer = snapshot.answer if snapshot is not None else ""
            question = snapshot.question if snapshot is not None else "interrupted turn"
            self._chat_area.add_system("[#e5c07b]已强制中断当前 Agent 活动，并记录中断摘要。[/]")
            self._side_panel.add_timeline_event("error", "Agent interrupted by double Escape")
            self._record_turn_summary(question, answer)
        except Exception:
            pass

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
        self._agent_worker = get_current_worker()
        self._streaming_msg_widget = None
        self._streaming_buffer = ""
        self._last_stream_update = 0.0
        run, _events, turn = self._chat_service.begin_turn(self._chat_session.id, text)
        self._current_run_id = run.id
        self._current_turn = turn
        if hasattr(self._agent, "set_cancel_checker"):
            self._agent.set_cancel_checker(turn.cancel_checker)
        if self._capability_runtime is not None:
            self._capability_runtime.refresh_if_stale()
            self._refresh_tools_panel()
            self._refresh_mcp_panel()
            self._refresh_skill_panel()

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
            while not event.wait(0.05):
                if turn.cancel_checker():
                    return False
            return result_holder[0]

        self._agent._confirm = _tui_confirm
        bridge = getattr(self.app, "_subagent_confirm", None)
        if bridge is not None:
            bridge.set_target(_tui_confirm)

        # ── Mount loading indicator ──
        loading = Static("[#fab283]● Working...[/]", id="loading-indicator")
        self._chat_area.mount(loading)
        self._chat_area.call_after_refresh(self._chat_area.scroll_end)

        def _on_agent_event(event, from_state, to_state):
            """Structured FSM event → TUI dispatch (non-blocking)."""
            self.app.call_from_thread(_apply_event, event, from_state, to_state)

        STRATEGY_LABELS = {
            "NATIVE_TOOLS": "原生工具",
            "JSON_MODE": "JSON模式",
            "PROMPT_JSON": "提示词JSON",
            "PLAIN_TEXT": "纯文本",
        }

        def _apply_event(event, from_state, to_state):
            if turn.cancel_checker():
                return
            self._chat_service.record_agent_event(run.id, event)
            from agentnexus.agents.react_types import ReActEventType as E
            etype = event.type
            strategy = event.payload.get("strategy")
            if strategy:
                try:
                    caps = self._agent.llm_client.capabilities
                    self._hud.update_capabilities(
                        supports_thinking=caps.supports_thinking,
                        strategy=STRATEGY_LABELS.get(strategy, strategy),
                    )
                    self._refresh_model_panel(STRATEGY_LABELS.get(strategy, strategy))
                except Exception:
                    pass
            if etype == E.STREAM_TOKEN:
                token = event.payload.get("token", "")
                if not self._streaming_msg_widget:
                    msg = ChatMessage("assistant", "")
                    self._chat_area.mount(msg)
                    self._streaming_msg_widget = msg
                    self._streaming_buffer = ""
                    self._last_stream_update = 0.0
                self._streaming_buffer += token
                now = time.monotonic()
                if now - self._last_stream_update >= 0.05:
                    content_w = self._streaming_msg_widget.query_one("#msg-content", Static)
                    content_w.update(Text(self._streaming_buffer))
                    self._chat_area.call_after_refresh(self._chat_area.scroll_end)
                    self._last_stream_update = now
            elif etype == E.TOOLS_FOUND:
                if self._streaming_msg_widget:
                    self._streaming_msg_widget.remove()
                    self._streaming_msg_widget = None
                    self._streaming_buffer = ""
                thought = event.payload.get("thought")
                if thought:
                    self._chat_area.add_system(
                        f"[#a78bfa]Thought:[/] [italic dim]{thought}[/]")
                    self._turn_thought_count += 1
            elif etype == E.ANSWER_THOUGHT:
                if self._streaming_msg_widget:
                    self._streaming_msg_widget.remove()
                    self._streaming_msg_widget = None
                    self._streaming_buffer = ""
                thought = event.payload.get("thought")
                if thought:
                    self._chat_area.add_system(
                        f"[#a78bfa]Thought:[/] [italic dim]{thought}[/]")
                    self._turn_thought_count += 1
            elif etype == E.TOOL_START:
                self._stop_spinner()
                tool_name = event.payload.get("name", "")
                self._current_tool_name = tool_name
                self._turn_tool_names.append(tool_name)
                self._current_tool_started_at = time.monotonic()
                widget = ToolCall(tool_name, result="executing...")
                self._chat_area.mount(widget)
                self._current_tool_widget = widget
                self._chat_area.call_after_refresh(self._chat_area.scroll_end)
                self._spinner_frames = cycle(
                    ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"])
                self._spinner_timer = self.set_interval(0.12, self._tick_spinner)
            elif etype == E.TOOL_DONE:
                self._stop_spinner()
                result = event.payload.get("result", "")
                if self._current_tool_started_at:
                    self._current_tool_started_at = 0.0
                if self._current_tool_widget:
                    tool_lower = self._current_tool_widget.tool_name.strip().lower()
                    use_markup = False
                    if tool_lower == "web_search":
                        result = condense_search_result(result)
                    elif tool_lower == "file_read":
                        result = condense_file_result(result)
                    elif tool_lower == "subagent_run":
                        result = format_subagent_result(result)
                    else:
                        # Try to extract and render diff with colors for structured results
                        diff_parts = extract_diff_parts(result)
                        if diff_parts:
                            message, diff_text = diff_parts
                            colored_diff = render_diff_with_colors(diff_text)
                            result = f"{message}\n\n[dim]Diff preview:[/dim]\n{colored_diff}"
                            use_markup = True
                        else:
                            result = summarize_tool_result(result)
                    self._current_tool_widget.update_result(result, markup=use_markup)
                    self._current_tool_widget = None
                if tool_lower in ("todo_add", "todo_update"):
                    self._refresh_todo_panel()
                if self._memory:
                    stm_tokens = self._memory.estimate_stm_tokens()
                    self._hud.update_context(current_tokens=stm_tokens)
            elif etype == E.THOUGHT_MISSING:
                self._chat_area.add_system(
                    "[#e5c07b][!] 模型未输出 Thought，要求重新思考…[/]")
            elif etype == E.RETRIES_LEFT:
                self._chat_area.add_system(
                    f"[#e5c07b][重试] {event.payload.get('reason', '')}[/]")
            elif etype == E.DEGRADED:
                self._stop_spinner()
                self._current_tool_widget = None
                label = STRATEGY_LABELS.get(strategy, strategy or "?")
                self._chat_area.add_system(f"[#e5c07b][策略降级] → {label}[/]")
                self._refresh_model_panel(label)

        self._agent._on_event = _on_agent_event

        def _run_with_trace():
            """Run agent in a traced context — each user input is its own trace."""
            trace_manager.configure(get_settings().traces_dir)
            trace_manager.start_trace(text)
            try:
                agent_question = self._prepare_agent_question(text)
                if turn.cancel_checker():
                    raise RuntimeError("cancelled")
                result = self._agent.run(agent_question, memory_manager=self._memory)
                if turn.cancel_checker():
                    raise RuntimeError("cancelled")
                return result.answer
            finally:
                trace_manager.end_trace()

        try:
            answer = await asyncio.to_thread(_run_with_trace)
        except Exception as e:
            self._stop_spinner()
            self._current_tool_widget = None
            if self._streaming_msg_widget:
                self._streaming_msg_widget.remove()
                self._streaming_msg_widget = None
                self._streaming_buffer = ""
            try:
                self._chat_area.query_one("#loading-indicator").remove()
            except Exception:
                pass
            if str(e) == "cancelled":
                reason = "用户中断或取消信号"
                record = turn.cancel(reason)
                answer = record.answer
                self._chat_area.add_system("[#e5c07b]Agent 活动已中断，中断摘要已写入会话。[/]")
            else:
                reason = "Agent 执行错误"
                record = turn.fail(reason, str(e))
                answer = record.answer
                self._chat_area.add_system(f"[#e06c75]错误: {e}[/]\n[dim]已记录失败摘要。[/]")
            try:
                self._side_panel.add_timeline_event("error", collapse_and_truncate(str(e), 80))
            except Exception:
                pass
            self._record_turn_summary(text, answer)
            self._chat_service.mark_processing(False)
            self._agent_worker = None
            self._current_turn = None
            self._current_run_id = ""
            if hasattr(self._agent, "set_cancel_checker"):
                self._agent.set_cancel_checker(None)
            self._refresh_todo_panel()
            self._drain_message_queue()
            return

        if turn.cancel_checker():
            if self._streaming_msg_widget:
                self._streaming_msg_widget.remove()
                self._streaming_msg_widget = None
                self._streaming_buffer = ""
            self._chat_service.mark_processing(False)
            self._agent_worker = None
            self._current_turn = None
            self._current_run_id = ""
            if hasattr(self._agent, "set_cancel_checker"):
                self._agent.set_cancel_checker(None)
            self._drain_message_queue()
            return

        # ── Remove loading indicator ──
        try:
            self._chat_area.query_one("#loading-indicator").remove()
        except Exception:
            pass

        if answer:
            loop = asyncio.get_running_loop()
            if self._streaming_msg_widget:
                # ── Real streaming: finalize with Markdown render ──
                content_w = self._streaming_msg_widget.query_one("#msg-content", Static)
                rendered = await loop.run_in_executor(None, Markdown, answer)
                content_w.update(rendered)
                self._streaming_msg_widget = None
                self._streaming_buffer = ""
            else:
                # ── Fallback: simulated typing effect ──
                msg_widget = ChatMessage("assistant", "")
                await self._chat_area.mount(msg_widget)
                self._chat_area.call_after_refresh(self._chat_area.scroll_end)

                msg_content = msg_widget.query_one("#msg-content", Static)

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
                if displayed:
                    msg_content.update(Text(displayed))

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
            self._record_turn_summary(text, answer)
            self._refresh_todo_panel()
            self._chat_service.mark_processing(False)
            self._agent_worker = None
        else:
            answer = turn.finish("").answer
            self._chat_area.add_system("[dim]Agent 未能得出答案，已记录本轮执行摘要。[/]")
            self._record_turn_summary(text, answer)
            self._chat_service.mark_processing(False)
            self._agent_worker = None
        self._current_turn = None
        self._current_run_id = ""
        if hasattr(self._agent, "set_cancel_checker"):
            self._agent.set_cancel_checker(None)
        self._drain_message_queue()

    def _drain_message_queue(self) -> None:
        """Process the next queued message if available."""
        if self._chat_service.queue_size > 0:
            next_item = self._chat_service.dequeue_message()
            if next_item is not None:
                _session_id, text = next_item
                self._chat_area.add_system("[dim]处理排队消息...[/]")
                self._chat_service.mark_processing(True)
                # Defer to allow the current @work(exclusive=True) worker to finish
                self.set_timer(0, lambda: self._run_agent(text))


def _format_ctx_window(tokens) -> str:
    if not isinstance(tokens, int) or tokens <= 0:
        return "unknown"
    if tokens >= 1_000_000:
        return f"{tokens / 1_000_000:.1f}m"
    return f"{tokens // 1000}k"
