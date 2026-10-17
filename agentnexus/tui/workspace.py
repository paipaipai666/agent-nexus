"""Terminal navigation and draft editing over the existing chat operations."""

import asyncio

from textual import work
from textual.widgets import Input

from agentnexus.tui.screens.inspector import InspectorScreen, task_summary
from agentnexus.tui.screens.interactions import Choice, ChoiceScreen, MessageEditor
from agentnexus.tui.widgets.input_bar import InputBar


class ChatWorkspace:
    """Keep keyboard navigation separate from execution and command dispatch."""

    def action_command_menu(self):
        plan = self._chat_service.is_plan_mode(self._chat_session.id)
        choices = [
            Choice("新建会话", "/session new"),
            Choice("关闭计划模式" if plan else "开启计划模式", "/plan off" if plan else "/plan on"),
            Choice("查看模型", "/model list"),
            Choice("思考强度", "/think ", "输入 low / medium / high / default", False),
            Choice("添加附件", "/attach add ", "输入文件路径", False),
            Choice("查看记忆", "/memory list"),
            Choice("知识库", "/kb list"),
            Choice("项目 Wiki", "/wiki list"),
            Choice("Skill", "/skill list"),
            Choice("MCP", "/mcp status"),
            Choice("设置", "/settings show"),
            Choice("任务恢复", "/recover status"),
        ]
        choices.extend(Choice(label, f"/theme {theme}") for label, theme in [
            ("主题 · 深色", "nexus-dark"), ("主题 · 浅色", "nexus-light"),
            ("主题 · 终端深色", "ansi-dark"), ("主题 · 终端浅色", "ansi-light"),
        ])
        from agentnexus.core.config import get_settings

        for provider in get_settings().llm_providers:
            for model in provider.models:
                selector = f"{provider.name}/{model.model_id}"
                choices.append(Choice(f"切换模型 · {selector}", f"/model select {selector}"))
        choices.extend(Choice(f"思考强度 · {effort}", f"/think {effort}") for effort in ("default", "none", "low", "medium", "high"))
        existing = {choice.value for choice in choices}
        choices.extend(Choice(description, command + " ", command, False)
                       for command, description, _aliases in self._command_catalog() if command not in existing)
        self.app.push_screen(ChoiceScreen("操作 · Ctrl+P", choices), self._choose_workspace_action)

    def _choose_workspace_action(self, choice):
        if choice is not None:
            if choice.execute:
                self._handle_command(choice.value)
            else:
                inp = self.query_one("#chat-input", Input)
                if inp.value and not inp.value.startswith("/"):
                    self._terminal.drafts[self._chat_session.id] = inp.value
                    self._terminal.save_draft(self._chat_session.id)
                inp.value = choice.value
                inp.cursor_position = len(inp.value)
        self.action_focus_input()

    @work(group="session-picker", exclusive=True)
    async def action_session_menu(self):
        try:
            result = await asyncio.to_thread(self._terminal.run, "/session", "list", self._chat_session.id)
            choices = [Choice("+ 新建会话", "/session new")]
            for item in result.value:
                sid = item.get("session_id") or item.get("id")
                if not sid:
                    continue
                prefix = "当前 · " if sid == self._chat_session.id else ""
                choices.append(Choice(prefix + str(item.get("preview") or item.get("title") or sid),
                                      f"/session switch {sid}", str(item.get("workspace_path") or item.get("workspace") or sid)))
            self.app.push_screen(ChoiceScreen("会话 · F2", choices), self._choose_workspace_action)
        except Exception as exc:
            self.app.notify(f"无法读取会话: {exc}", severity="error")

    def action_toggle_inspector(self):
        if self.size.width >= 112:
            self.toggle_class("has-inspector")
        else:
            self.action_task_record()

    def on_resize(self, event):
        if event.size.width < 112:
            self.remove_class("has-inspector")

    @work(group="message-editor", exclusive=True)
    async def action_edit_message(self, text=None):
        sid = self._chat_session.id
        inp = self.query_one("#chat-input", Input)
        initial = text if text is not None else inp.value or self._terminal.drafts.get(sid, "")
        outcome, draft = await self.app.push_screen_wait(MessageEditor(initial))
        if outcome == "send":
            self._terminal.drafts.pop(sid, None)
            if sid == self._chat_session.id:
                self.on_input_bar_app_submit(InputBar.AppSubmit(draft))
            else:
                self._submit_session_message({"text": draft, "attachments": self._attachments.pop(sid, None)}, sid)
        else:
            self._terminal.drafts[sid] = draft
            if sid == self._chat_session.id:
                inp.value = ""
        self._terminal.save_draft(sid)
        self._refresh_workspace()
        self.action_focus_input()

    def on_input_bar_editor_requested(self, event):
        self.action_edit_message(event.text)

    def _refresh_workspace(self):
        if not self.is_mounted:
            return
        sid = self._chat_session.id
        self._chat_input.update_state(
            model=str(getattr(self._agent, "model_id", "未配置")),
            thinking=self._chat_service.get_thinking_effort(sid) or "默认",
            plan=self._chat_service.is_plan_mode(sid), attachments=self._attachments.get(sid, []),
            draft=self._terminal.drafts.get(sid, ""), queued=sid in self._running_sessions,
        )
        children = self._chat_service.list_subagents(sid)
        self.query_one("#collaboration-tree").update_children(children)
        self._side_panel.update_collaboration(children)
        recovery = self._chat_service.get_session_recovery(sid)
        self.query_one("#task-strip").display = bool(recovery)
        self.query_one("#task-summary").update(task_summary(recovery))
        if recovery and sid in self._running_sessions:
            self.query_one("#task-summary").update(task_summary({**recovery, "status": "running" if recovery.get("status") != "interrupted" else "interrupted"}))
        for action in ("undo", "redo"):
            self.query_one(f"#{action}-checkpoint").disabled = sid in self._running_sessions or not getattr(self._hud, f"_can_{action}")

    def action_task_record(self):
        sid = self._chat_session.id
        self.app.push_screen(InspectorScreen(self._chat_service, sid, self._terminal), lambda _: self.action_focus_input())

    def _refresh_session_metrics(self):
        usage = getattr(self._agent, "total_usage", {})
        usage = usage if isinstance(usage, dict) else {}
        self._hud.update_context(self._memory.estimate_stm_tokens() if self._memory else 0,
                                 usage.get("input_tokens", 0), usage.get("output_tokens", 0))
        caps = getattr(getattr(self._agent, "llm_client", None), "capabilities", None)
        self._hud.update_capabilities(bool(getattr(caps, "supports_thinking", False)),
                                      "原生工具" if getattr(caps, "supports_tool_calling", False) else "JSON模式")

    @work(group="welcome-sessions", exclusive=True, exit_on_error=False)
    async def _load_recent_session(self):
        sid = self._chat_session.id
        result = await asyncio.to_thread(self._terminal.run, "/session", "list", sid)
        if sid == self._chat_session.id:
            recent = next((item for item in result.value if item["session_id"] != sid and item.get("preview")), None)
            welcome = self._chat_area.query_one("#welcome")
            welcome.recent = " ".join(recent["preview"].split())[:48] if recent else ""
            welcome.refresh(layout=True)
