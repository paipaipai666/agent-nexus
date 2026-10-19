"""Terminal navigation and draft editing over the existing chat operations."""

import asyncio
from sqlite3 import Error as StorageError

from textual import work

from agentnexus.tui.activity import session_activity
from agentnexus.tui.commands import COMMAND_HELP
from agentnexus.tui.screens.inspector import InspectorScreen
from agentnexus.tui.screens.interactions import Choice, ChoiceScreen, ParameterScreen


class ChatWorkspace:
    """Keep keyboard navigation separate from execution and command dispatch."""

    def action_command_menu(self):
        self._choose_workspace_action(Choice("", "@root"))

    def _root_choices(self):
        plan = self._chat_service.is_plan_mode(self._chat_session.id)
        choices = [
            Choice("会话 · 新建", "/session new", "在当前工作区开始新任务"),
            Choice("会话 · 切换", "@sessions", "F2"),
            Choice("任务 · 详情与协作", "@task", "F3 · 查看子代理、回复问题、检查执行记录"),
            Choice("任务 · 撤销上一检查点", "/undo", disabled_reason="当前任务正在执行" if self._chat_session.id in self._running_sessions else "没有可撤销的检查点" if not self._hud._can_undo else ""),
            Choice("任务 · 重做下一检查点", "/redo", disabled_reason="当前任务正在执行" if self._chat_session.id in self._running_sessions else "没有可重做的检查点" if not self._hud._can_redo else ""),
            Choice("计划 · 关闭计划模式" if plan else "计划 · 开启计划模式", "/plan off" if plan else "/plan on"),
            Choice("模型 · 切换", "@models", str(getattr(self._agent, "model_id", "未配置"))),
            Choice("思考强度 · 选择", "@thinking", self._chat_service.get_thinking_effort(self._chat_session.id) or "默认"),
            Choice("附件 · 添加附件", "@attach", "输入文件路径，保留正在编辑的消息"),
            Choice("附件 · 管理", "@attachments"),
            Choice("资料 · 记忆、知识库与 Wiki", "@knowledge"),
            Choice("能力 · 选择 Skill", "@skills"),
            Choice("能力 · MCP 状态", "/mcp status"),
            Choice("设置 · 查看配置", "/settings show"),
            Choice("任务恢复 · 保存、继续与检查", "@recovery"),
            Choice("外观 · 选择主题", "@themes"),
            Choice("全部命令 · 高级操作", "@commands", "完整功能与参数帮助"),
        ]
        choices.extend(self._workspace_choices("@knowledge", self._chat_session.id)[1])
        values = {choice.value for choice in choices}
        choices.extend(Choice(f"命令 {command}", command, description, False)
                       for command, description, _ in self._command_catalog() if command not in values)
        return choices

    @work(group="workspace-action", exclusive=True)
    async def _choose_workspace_action(self, choice):
        sid = self._chat_session.id
        parents = []
        query, selected = "", None
        while choice is not None and sid == self._chat_session.id:
            if choice.value.startswith("@"):
                route = choice.value
                if route == "@task":
                    self.action_task_record()
                    return
                if route == "@attach":
                    value = await self.app.push_screen_wait(ParameterScreen("添加附件", "文件路径，可包含空格。最多 10 个附件。"))
                    result = Choice("添加附件", "/attach add " + value) if value else None
                elif route.startswith("@parameter:"):
                    command, prompt = route.removeprefix("@parameter:").split("|", 1)
                    value = await self.app.push_screen_wait(ParameterScreen(choice.label, prompt))
                    result = Choice(choice.label, command + " " + value) if value else None
                else:
                    if route == "@sessions":
                        title, choices, current = "会话 · F2", await self._session_choices(), None
                    else:
                        title, choices, current = self._workspace_choices(route, sid)
                    picker = ChoiceScreen(title, choices, selected_value=selected or current, query=query)
                    result = await self.app.push_screen_wait(picker)
                    if result is not None:
                        parents.append((choice, picker.search_text, picker.selected_value))
                query, selected = "", None
                if result is not None:
                    choice = result
                    continue
            elif not choice.execute:
                command = choice.value.strip()
                argument = await self.app.push_screen_wait(ParameterScreen(command, choice.description or "输入命令参数", allow_empty=True))
                if argument is not None:
                    choice = Choice(choice.label, command + " " + argument)
                    continue
            else:
                self._execute_workspace_choice(choice, sid)
                break
            if parents:
                choice, query, selected = parents.pop()
            else:
                break
        self.action_focus_input()

    def _execute_workspace_choice(self, choice, sid):
        if choice.value.startswith("/theme"):
            self._handle_command(choice.value)
        else:
            command, _, args = choice.value.partition(" ")
            if command not in COMMAND_HELP:
                self._handle_command(choice.value)
            else:
                self._run_terminal_command(command, args, sid, presentation="record")

    def _workspace_choices(self, route, sid):
        from agentnexus.core.config import get_settings

        if route == "@root":
            return "操作 · Ctrl+P", self._root_choices(), None
        if route == "@models":
            choices = [Choice(f"{provider.name} / {model.model_id}", f"/model select {provider.name}/{model.model_id}")
                       for provider in get_settings().llm_providers for model in provider.models]
            choices.append(Choice("查看模型与配置", "/model list"))
            return "选择模型", choices, f"/model select {getattr(self._agent, 'model_id', '')}"
        if route == "@thinking":
            current = self._chat_service.get_thinking_effort(sid) or "default"
            labels = {"default": "跟随模型默认", "none": "关闭额外思考", "low": "轻量", "medium": "标准", "high": "深入"}
            return "选择思考强度", [Choice(f"{label} · {effort}", f"/think {effort}") for effort, label in labels.items()], f"/think {current}"
        if route == "@attachments":
            choices = [Choice("添加附件", "@attach")]
            choices.extend(Choice(f"移除 {item['name']}", f"/attach remove {i}") for i, item in enumerate(self._attachments.get(sid, []), 1))
            return "消息附件", choices, None
        if route == "@themes":
            themes = [("深色", "nexus-dark"), ("浅色", "nexus-light"), ("终端深色", "ansi-dark"), ("终端浅色", "ansi-light")]
            return "选择主题", [Choice(label, f"/theme {theme}") for label, theme in themes], f"/theme {self.app.theme}"
        if route == "@skills":
            choices = [Choice(name, f"/skill use {key}", description) for key, name, description in self._available_skill_summary()]
            choices.append(Choice("查看 Skill 状态", "/skill status"))
            return "选择 Skill", choices, None
        if route == "@recovery":
            return "恢复当前任务", [Choice("查看保存状态", "/recover status"), Choice("重试保存", "/recover save"),
                                   Choice("继续未完成任务", "/recover continue", "将继续请求放入草稿，检查后发送"),
                                   Choice("重试原始提交", "/recover retry", "沿用请求 ID，避免重复执行")], None
        if route == "@knowledge":
            choices = []
            for title, command in [("记忆", "/memory"), ("知识库", "/kb"), ("项目 Wiki", "/wiki")]:
                choices.append(Choice(f"{title} · 浏览", command + " list"))
                action = "query" if command == "/wiki" else "search"
                choices.append(Choice(f"{title} · 搜索", f"@parameter:{command} {action}|输入搜索内容"))
                if command != "/memory":
                    choices.append(Choice(f"{title} · 导入文件", f"@parameter:{command} ingest|输入文件路径，可包含空格"))
            return "资料与检索", choices, None
        return "全部命令", [Choice(description, command, COMMAND_HELP.get(command.split()[0], description), False) for command, description, _ in self._command_catalog()], None

    def action_session_menu(self):
        self._choose_workspace_action(Choice("", "@sessions"))

    async def _session_choices(self):
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
            return choices
        except Exception as exc:
            self.app.notify(f"无法读取会话: {exc}", severity="error")
            return []

    def action_toggle_inspector(self):
        self.action_task_record()

    def on_resize(self, event):
        if event.size.width < 112:
            self.remove_class("has-inspector")

    def action_edit_message(self):
        self._chat_input.toggle_multiline()
        self._update_command_suggestions(self.query_one("#chat-input").text)

    def action_choose_model(self):
        self._choose_workspace_action(Choice("", "@models"))

    def action_choose_thinking(self):
        self._choose_workspace_action(Choice("", "@thinking"))

    def action_choose_attachment(self):
        route = "@attachments" if self._attachments.get(self._chat_session.id) else "@attach"
        self._choose_workspace_action(Choice("", route))

    def action_toggle_plan(self):
        enabled = self._chat_service.is_plan_mode(self._chat_session.id)
        self._choose_workspace_action(Choice("计划模式", "/plan off" if enabled else "/plan on"))

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
        failure = ""
        children, recovery = [], None
        try:
            children = self._chat_service.list_subagents(sid)
        except (OSError, StorageError) as exc:
            failure = str(exc)
        try:
            recovery = self._chat_service.get_session_recovery(sid)
        except (OSError, StorageError) as exc:
            failure = str(exc)
        self._side_panel.update_collaboration(children)
        self._activity_children, self._activity_recovery, self._activity_failure = children, recovery, failure
        self._refresh_activity()
        self._refresh_action_hints()

    def _refresh_activity(self):
        sid = self._chat_session.id
        active = sid in self._running_sessions or self._chat_service.is_session_processing(sid)
        activity = session_activity(
            active=active, progress=self._activity_runs.get(sid),
            children=getattr(self, "_activity_children", ()), recovery=getattr(self, "_activity_recovery", None),
            failure=getattr(self, "_activity_failure", ""), compacting=sid in self._compacting_sessions,
        )
        indicator = self.query_one("#agent-activity")
        indicator.update_activity(activity)
        if getattr(self, "_activity_failure", ""):
            indicator.tooltip += " · " + self._activity_failure
        self._chat_input.fit_footer()

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
