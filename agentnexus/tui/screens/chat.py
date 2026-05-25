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
from agentnexus.skills import (
    SkillEntry,
    SkillRegistry,
    format_tool_policy_summary,
    validate_session_profile,
)
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

    def __init__(self, agent, memory, version, mcp_manager=None, skill_service=None):
        super().__init__()
        self._agent = agent
        self._memory = memory
        self._version = version
        self._mcp_manager = mcp_manager
        self._skill_service = skill_service
        self._running = False
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
        self._hud = HUD(id="hud")
        self._chat_input = InputBar(id="input-area")
        yield self._hud
        yield self._chat_input

    def _render_top_bar(self) -> str:
        model = getattr(self._agent, "model_id", "v4-flash") if self._agent else "v4-flash"
        branch = self._version.status().get("branch", "main") if self._version else "main"

        left = "[#6ba5f2]AgentNexus[/] [dim]workspace[/]"
        center = f"[dim]branch[/] {branch}  [dim]model[/] [#6ba5f2]{model}[/]"
        right = "[dim]^H help  ^L clear  Esc focus[/]"

        return f"{left}  {center}  {right}"

    def on_mount(self):
        self._chat_area.add_system("[#6ba5f2]AgentNexus ready[/] [dim]Ask a question or type /help.[/]")
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
            "[dim]命令:[/] /help  /undo  /redo  /log [--all]  /branch <名>\n"
            "       /checkout <ref>  /diff [ref1] [ref2]  /status  /mcp\n"
            "       /skill [status|list|use <id> [--default]|default <id>|validate [id]|reset]  "
            "/clear [--all]  /compact [指令]  /stats"
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
        self._hud.update_version(
            st.get("branch", "main"),
            st["head"]["id"] if st.get("head") else "---",
            st.get("can_undo", False),
            st.get("can_redo", False),
        )
        try:
            self._side_panel.update_version(
                st.get("branch", "main"),
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
        elif cmd == "/skill":
            self._handle_skill_command(arg)
        else:
            if self._handle_dynamic_skill_command(cmd, arg):
                return
            self._chat_area.add_system(f"[dim]未知: {cmd}[/]")

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
        from agentnexus.core.config import _load_yaml, _write_yaml_config

        data = _load_yaml()
        data["default_skill"] = qualified_id
        _write_yaml_config(data)

    @staticmethod
    def _clear_default_skill() -> None:
        from agentnexus.core.config import _load_yaml, _write_yaml_config

        data = _load_yaml()
        data.pop("default_skill", None)
        _write_yaml_config(data)

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

    def _handle_dynamic_skill_command(self, cmd: str, arg: str) -> bool:
        if not cmd.startswith("/") or not cmd.endswith("-skill"):
            return False
        target = cmd[1:-6].strip()
        if not target:
            return False
        instruction = arg.strip()
        if not instruction:
            self._chat_area.add_system(f"[dim]用法: /{target}-skill <指令>[/]")
            return True
        entry = self._resolve_dynamic_skill(target)
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
        self._running = True
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

    def _refresh_tools_panel(self):
        try:
            registry = getattr(getattr(self._agent, "tool_executor", None), "registry", None)
            tools = []
            if registry is not None:
                for name, (meta, _) in registry._tools.items():
                    risk = getattr(meta.risk_level, "value", str(meta.risk_level))
                    tools.append({"name": name, "risk": risk})
            self._side_panel.update_tools(tools)
        except Exception:
            pass

    def _refresh_model_panel(self, strategy: str = ""):
        try:
            model = getattr(self._agent, "model_id", "unknown") if self._agent else "unknown"
            max_tokens = getattr(getattr(self, "_hud", None), "ctx_max", None)
            if not isinstance(max_tokens, int) or max_tokens <= 0:
                caps = getattr(getattr(self._agent, "llm_client", None), "capabilities", None)
                max_tokens = getattr(caps, "max_context_tokens", None)
            ctx = _format_ctx_window(max_tokens)
            self._side_panel.update_model(model=model, ctx=ctx, strategy=strategy)
        except Exception:
            pass

    def _record_turn_summary(self, question: str, answer: str = ""):
        try:
            tools = ", ".join(dict.fromkeys(self._turn_tool_names)) or "no tools"
            thought_part = f"{self._turn_thought_count} thoughts"
            answer_part = _plain_summary(answer, 32) if answer else "no answer"
            question_part = _plain_summary(question, 24)
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
            self._side_panel.add_timeline_event(marker, _plain_summary(text, 80))
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
                        _plain_summary(
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

    @staticmethod
    def _format_subagent_result(text: str) -> str:
        """Format subagent_run JSON output into a readable delegation summary."""
        import json

        try:
            payload = json.loads(text)
        except Exception:
            return text[:500]

        if not isinstance(payload, dict):
            return text[:500]

        role = payload.get("role", "general")
        status = payload.get("status", "unknown")
        steps_used = payload.get("steps_used", 0)
        allowed_tools = payload.get("allowed_tools", []) or []
        answer = str(payload.get("answer", "") or "").strip()
        summary = str(payload.get("summary", "") or "").strip()

        lines = [
            f"[子代理] role={role} status={status} steps={steps_used}",
            f"tools: {', '.join(allowed_tools) if allowed_tools else '-'}",
        ]
        if answer:
            lines.append(f"answer: {answer[:400]}")
        elif summary:
            lines.append(f"summary: {summary[:400]}")
        return "\n".join(lines)

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
            if etype == E.TOOLS_FOUND:
                thought = event.payload.get("thought")
                if thought:
                    self._chat_area.add_system(
                        f"[#a78bfa]Thought:[/] [italic dim]{thought}[/]")
                    self._turn_thought_count += 1
            elif etype == E.ANSWER_THOUGHT:
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
                    if tool_lower == "web_search":
                        result = self._condense_search_result(result)
                    elif tool_lower == "file_read":
                        result = self._condense_file_result(result)
                    elif tool_lower == "subagent_run":
                        result = self._format_subagent_result(result)
                    self._current_tool_widget.update_result(result)
                    self._current_tool_widget = None
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
                result = self._agent.run(agent_question, memory_manager=self._memory)
                return result.answer
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
            try:
                self._side_panel.add_timeline_event("error", _plain_summary(str(e), 80))
            except Exception:
                pass
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
            await self._chat_area.mount(msg_widget)
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
            self._record_turn_summary(text, answer)
        else:
            self._chat_area.add_system("[dim]Agent 未能得出答案。[/]")
            self._record_turn_summary(text, "")
        self._running = False


def _plain_summary(text: str, limit: int) -> str:
    clean = " ".join(str(text or "").split())
    if len(clean) <= limit:
        return clean
    return clean[: max(0, limit - 1)] + "…"


def _format_ctx_window(tokens) -> str:
    if not isinstance(tokens, int) or tokens <= 0:
        return "unknown"
    if tokens >= 1_000_000:
        return f"{tokens / 1_000_000:.1f}m"
    return f"{tokens // 1000}k"
