"""Live task inspection and durable child-agent communication."""

import asyncio
import json
import uuid
from sqlite3 import Error as StorageError

from rich.text import Text
from textual import on, work
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Label, OptionList, Static, TextArea
from textual.widgets.option_list import Option

from agentnexus.tui.actions import ActionState, ContextFooter, TerminalAction
from agentnexus.tui.screens.interactions import ActionScreen, Choice, ChoiceScreen, MessageEditor, RecordScreen
from agentnexus.tui.widgets.collaboration import child_label


def task_summary(recovery):
    """Distinguish execution outcome from durable save state."""
    if not recovery:
        return Text("尚无任务 · 输入消息开始", style="dim")
    states = {"running": "执行中", "finished": "已完成", "completed": "已完成", "failed": "失败", "interrupted": "已中断", "empty_answer": "无回答"}
    status = "正在停止" if recovery.get("execution_active") and recovery.get("status") == "interrupted" else states.get(recovery.get("status"), recovery.get("status", ""))
    saved = "已保存" if recovery.get("persisted") else "尚未保存"
    return Text(f"{status} · {saved}" + (f" · {recovery['save_error']}" if recovery.get("save_error") else ""))


class InspectorScreen(ActionScreen[None]):
    """Inspect, reply to, supplement and stop the selected child task."""

    ACTIONS = (
        TerminalAction("reply", "回复", "r", "reply"),
        TerminalAction("instruct", "补充", "i", "instruct"),
        TerminalAction("record", "记录", "l", "show_record"),
        TerminalAction("stop", "停止", "x", "stop_child"),
        TerminalAction("region", "切换区域", "tab,shift+tab", "cycle_region", key_display="Tab"),
        TerminalAction("close", "返回", "escape", "close", key_display="Esc"),
    )
    BINDINGS = [action.binding() for action in ACTIONS]
    CONTROLS = {"child-reply": "reply", "child-send": "instruct", "child-stop": "stop", "task-record": "record"}
    DEFAULT_CSS = """
    InspectorScreen { align: center middle; background: $background; }
    InspectorScreen > Vertical { width: 100%; height: 100%; background: $background; border: none; padding: 0 2; }
    InspectorScreen Label { height: 1; color: $text-muted; }
    InspectorScreen #task-title { color: $foreground; text-style: bold; margin-bottom: 1; }
    InspectorScreen #task-detail { height: 1; text-wrap: nowrap; text-overflow: ellipsis; color: $text-muted; }
    InspectorScreen #task-todos { height: auto; max-height: 4; overflow-y: auto; }
    InspectorScreen #task-content { layout: vertical; height: 1fr; }
    InspectorScreen OptionList { width: 100%; height: auto; max-height: 5; min-height: 1; border: none; background: transparent; }
    InspectorScreen #child-list > .option-list--option-highlighted { color: $foreground; background: $primary 12%; text-style: bold; }
    InspectorScreen #child-detail { height: 1fr; padding: 0; border: none; background: transparent; }
    InspectorScreen #task-controls { height: 1; margin-top: 1; }
    InspectorScreen #task-controls Button { width: 1fr; height: 1; min-height: 1; min-width: 8; padding: 0; border: none; background: transparent; text-style: none; }
    InspectorScreen #task-controls Button:focus, InspectorScreen #task-controls Button:hover { color: $primary; background: $primary 12%; }
    InspectorScreen ContextFooter { height: 1; color: $text-muted; }
    """

    def __init__(self, chat, sid, terminal):
        super().__init__()
        self.chat, self.sid, self.terminal = chat, sid, terminal
        self.children_by_id = {}
        self.selected_id = None
        self._requests = terminal.collaboration_requests.setdefault(sid, {})
        self._drafts = terminal.collaboration_drafts.setdefault(sid, {})
        self._disabled_reasons = {}
        self._recovery = None
        self._detail_identity = None

    def compose(self):
        with Vertical():
            yield Label("AgentNexus / 任务与协作", id="task-title")
            yield Static("", id="task-detail")
            yield Static("", id="task-todos")
            with Vertical(id="task-content"):
                yield OptionList(id="child-list", compact=True)
                yield TextArea("", id="child-detail", read_only=True, soft_wrap=True)
            with Horizontal(id="task-controls"):
                actions = {action.id: action for action in self.ACTIONS}
                for control, action_id in self.CONTROLS.items():
                    action = actions[action_id]
                    yield Button(f"{action.display_key} {action.title}", id=control)
            yield ContextFooter(id="task-actions")

    def on_mount(self):
        self.refresh_data()
        self.query_one("#child-list" if self.children_by_id else "#child-detail").focus()
        self.set_interval(1, self.refresh_data)

    def refresh_data(self):
        try:
            self._refresh_data()
        except (OSError, StorageError) as exc:
            self.query_one("#task-detail", Static).update(Text(f"任务状态不可用 · {exc}"))
            self._disabled_reasons = dict.fromkeys(("reply", "instruct", "stop", "record"), f"任务状态不可用 · {exc}")
            self._sync_actions()

    def _refresh_data(self):
        children = self.chat.list_subagents(self.sid)
        children.sort(key=lambda child: (not child.get("waiting_questions"), child.get("status") not in {"thinking", "tool_calling", "waiting", "cancelling"}))
        self._recovery = self.chat.get_session_recovery(self.sid)
        waiting = sum(child.get("waiting_questions", 0) for child in children)
        self.query_one("#task-detail", Static).update(Text(f"待答复 {waiting} · 子任务 {len(children)} · ") + task_summary(self._recovery))
        todo = getattr(self.chat.session_agent(self.sid), "_todo_list", None)
        items = todo.list_items() if todo is not None else []
        self.query_one("#task-todos", Static).update(Text("\n".join(f"{item.status} · {item.description}" for item in items)))
        self.query_one("#task-todos").display = bool(items)
        options = self.query_one("#child-list", OptionList)
        options.display = bool(children)
        new_ids = [child["subagent_id"] for child in children]
        if new_ids != list(self.children_by_id):
            options.clear_options()
            for child in children:
                options.add_option(Option(Text(child_label(child, include_progress=False), no_wrap=True, overflow="ellipsis"), id=child["subagent_id"]))
            if new_ids:
                options.highlighted = new_ids.index(self.selected_id) if self.selected_id in new_ids else 0
                self.selected_id = new_ids[options.highlighted]
        else:
            for child in children:
                options.replace_option_prompt(child["subagent_id"], Text(child_label(child, include_progress=False), no_wrap=True, overflow="ellipsis"))
        self.children_by_id = {child["subagent_id"]: child for child in children}
        self.show_child()

    @on(OptionList.OptionHighlighted)
    def select_child(self, event):
        self.selected_id = event.option.id
        self.show_child()

    def _messages(self, child):
        from agentnexus.observability.timeline import get_timeline_store

        return get_timeline_store().list_collaboration_messages(self.sid, child["subagent_id"], run_id=child.get("run_id"))

    def show_child(self):
        child = self.children_by_id.get(self.selected_id)
        active = child and child.get("collaborative") and child.get("status") in {"thinking", "tool_calling", "waiting"}
        messages = self._messages(child) if child else []
        questions = [m for m in messages if m["kind"] == "question" and m["question_state"] == "open" and m["child_run_id"] == child.get("child_run_id")]
        self._disabled_reasons = {
            "reply": "当前子任务没有待回复的问题" if not active or not questions else "",
            "instruct": "当前子任务不支持接收补充指令" if not active else "",
            "stop": "请选择一个运行中的子任务" if not child or child.get("status") in {"completed", "failed", "interrupted", "cancelling"} else "",
        }
        self._sync_actions()
        if child:
            lines = [child_label(child), "", child.get("task", "")]
            if child.get("error"):
                lines.extend(["", child["error"]])
            for message in messages:
                state = message.get("question_state") or message.get("delivery", "")
                labels = {"question": "问题", "reply": "回复", "instruction": "补充指令", "progress": "进度", "open": "待回复", "answered": "已回复", "closed": "已关闭", "stale": "已失效", "queued": "待送达", "delivered": "已送达", "rejected": "已拒绝"}
                lines.extend(["", f"{labels.get(message['kind'], message['kind'])} #{message['id']} · {labels.get(state, state)}", message["content"]])
            content = "\n".join(lines)
        else:
            recovery = self._recovery
            content = (recovery.get("question", "") + "\n\n" + recovery.get("answer", "")) if recovery else "当前会话没有子任务。\n执行记录可查看保存状态、检查点和任务恢复信息。"
        detail = self.query_one("#child-detail", TextArea)
        if detail.text != content:
            identity = (self.selected_id, child.get("child_run_id") if child else None)
            selection, offset = detail.selection, detail.scroll_offset
            following = detail.scroll_y >= detail.max_scroll_y
            detail.load_text(content)
            if identity == self._detail_identity:
                detail.selection = selection
                if following:
                    detail.call_after_refresh(lambda: detail.scroll_end(animate=False))
                else:
                    detail.call_after_refresh(lambda: detail.scroll_to(x=offset.x, y=offset.y, animate=False))
            self._detail_identity = identity

    def action_states(self):
        return [ActionState(state.action, self._disabled_reasons.get(state.action.id, "")) for state in super().action_states()]

    def _sync_actions(self):
        states = {state.action.id: state for state in self.action_states()}
        for button, action_id in self.CONTROLS.items():
            control = self.query_one(f"#{button}", Button)
            control.disabled = bool(states[action_id].disabled_reason)
            control.tooltip = states[action_id].disabled_reason or None
        self.query_one(ContextFooter).show_actions([states[key] for key in ("region", "close", "help")], prefix="↑↓ 选择子任务")

    async def action_perform(self, action_id):
        self.refresh_data()
        await super().action_perform(action_id)

    def action_cycle_region(self):
        self.query_one("#child-list" if self.query_one("#child-detail").has_focus and self.children_by_id else "#child-detail").focus()

    def action_reply(self):
        self.message_child(dict(self.children_by_id[self.selected_id]), reply=True)

    def action_instruct(self):
        self.message_child(dict(self.children_by_id[self.selected_id]), reply=False)

    @on(Button.Pressed)
    async def activate_control(self, event):
        if event.button.id in self.CONTROLS:
            event.stop()
            await self.action_perform(self.CONTROLS[event.button.id])

    @work(group="child-message", exclusive=True)
    async def message_child(self, child, *, reply):
        question = None
        if reply:
            questions = [m for m in self._messages(child) if m["kind"] == "question" and m["question_state"] == "open" and m["child_run_id"] == child["child_run_id"]]
            if not questions:
                return
            if len(questions) > 1:
                choice = await self.app.push_screen_wait(ChoiceScreen("选择要回复的问题", [Choice(m["content"], str(m["id"])) for m in questions]))
                if choice is None:
                    return
                question = next(m for m in questions if str(m["id"]) == choice.value)
            else:
                question = questions[0]
        identity = (child["subagent_id"], child["child_run_id"], question["id"] if question else None)
        title = f"回复：{question['content']}" if question else f"补充指令：{child['name']}"
        outcome, content = await self.app.push_screen_wait(MessageEditor(self._drafts.get(identity, ""), title))
        self._drafts[identity] = content
        if outcome != "send":
            return
        request_id = self._requests.setdefault((*identity, content), uuid.uuid4().hex)
        try:
            receipt = await asyncio.to_thread(self.chat.send_subagent_message, self.sid, child["subagent_id"], content, request_id, child["child_run_id"], identity[-1])
            self._requests.pop((*identity, content), None)
            self._drafts.pop(identity, None)
            self.app.notify(f"消息 #{receipt['id']} · {receipt['delivery']}")
            self.refresh_data()
        except Exception as exc:
            self.app.notify(f"发送失败，内容已保留: {exc}", severity="error")

    def action_stop_child(self):
        accepted = self.chat.cancel_subagent(self.sid, self.selected_id)
        self.app.notify("已请求停止子任务" if accepted else "子任务已经结束")
        self.refresh_data()

    @work(group="task-record")
    async def action_show_record(self):
        recovery = self.chat.get_session_recovery(self.sid)
        result = await asyncio.to_thread(self.terminal.run, "/subagents", f"show {self.selected_id}", self.sid) if self.selected_id else None
        version = self.chat.session_version(self.sid)
        record = {"session_id": self.sid, "recovery": recovery, "checkpoint": version.status() if version else None,
                  "child": result.value if result else None}
        self.app.push_screen(RecordScreen("执行记录", json.dumps(record, ensure_ascii=False, indent=2, default=str)))

    def action_close(self):
        self.dismiss(None)
