"""Live task inspection and durable child-agent communication."""

import asyncio
import json
import uuid

from rich.text import Text
from textual import on, work
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, OptionList, Static, TextArea
from textual.widgets.option_list import Option

from agentnexus.tui.screens.interactions import Choice, ChoiceScreen, MessageEditor, RecordScreen
from agentnexus.tui.widgets.collaboration import child_label


def task_summary(recovery):
    """Distinguish execution outcome from durable save state."""
    if not recovery:
        return Text("尚无任务 · 输入消息开始", style="dim")
    states = {"running": "执行中", "finished": "已完成", "completed": "已完成", "failed": "失败", "interrupted": "已中断", "empty_answer": "无回答"}
    status = "正在停止" if recovery.get("execution_active") and recovery.get("status") == "interrupted" else states.get(recovery.get("status"), recovery.get("status", ""))
    saved = "已保存" if recovery.get("persisted") else "尚未保存"
    return Text(f"{status} · {saved}" + (f" · {recovery['save_error']}" if recovery.get("save_error") else ""))


class InspectorScreen(ModalScreen[None]):
    """Inspect, reply to, supplement and stop the selected child task."""

    BINDINGS = [("escape", "close", "返回")]
    DEFAULT_CSS = """
    InspectorScreen { align: center middle; }
    InspectorScreen > Vertical { width: 96%; height: 94%; background: $surface; border: solid $primary; padding: 0 1; }
    InspectorScreen Label { height: 1; color: $text-muted; }
    InspectorScreen #task-detail { height: auto; max-height: 3; }
    InspectorScreen OptionList { height: auto; max-height: 6; min-height: 2; border: none; background: transparent; }
    InspectorScreen TextArea { height: 1fr; border: none; background: transparent; }
    InspectorScreen Horizontal { height: 3; }
    InspectorScreen Button { width: 1fr; min-width: 8; }
    """

    def __init__(self, chat, sid, terminal):
        super().__init__()
        self.chat, self.sid, self.terminal = chat, sid, terminal
        self.children_by_id = {}
        self.selected_id = None
        self._requests = terminal.collaboration_requests.setdefault(sid, {})
        self._drafts = terminal.collaboration_drafts.setdefault(sid, {})

    def compose(self):
        with Vertical():
            yield Label("任务与协作 · F3")
            yield Static("", id="task-detail")
            yield OptionList(id="child-list", compact=True)
            yield TextArea("", id="child-detail", read_only=True, soft_wrap=True)
            with Horizontal():
                yield Button("回复问题", id="child-reply")
                yield Button("补充指令", id="child-send")
                yield Button("停止子任务", id="child-stop")
                yield Button("执行记录", id="task-record")
            yield Label("Tab 切换区域 · ↑↓ 选择子任务 · Esc 返回")

    def on_mount(self):
        self.refresh_data()
        self.set_interval(1, self.refresh_data)

    def refresh_data(self):
        children = self.chat.list_subagents(self.sid)
        self.query_one("#task-detail", Static).update(task_summary(self.chat.get_session_recovery(self.sid)))
        options = self.query_one("#child-list", OptionList)
        new_ids = [child["subagent_id"] for child in children]
        if new_ids != list(self.children_by_id):
            options.clear_options()
            for child in children:
                options.add_option(Option(Text(child_label(child)), id=child["subagent_id"]))
            if new_ids:
                options.highlighted = new_ids.index(self.selected_id) if self.selected_id in new_ids else 0
                self.selected_id = new_ids[options.highlighted]
        else:
            for child in children:
                options.replace_option_prompt(child["subagent_id"], Text(child_label(child)))
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
        self.query_one("#child-stop", Button).disabled = not child or child.get("status") in {"completed", "failed", "interrupted", "cancelling"}
        active = child and child.get("collaborative") and child.get("status") in {"thinking", "tool_calling", "waiting"}
        self.query_one("#child-send", Button).disabled = not active
        messages = self._messages(child) if child else []
        questions = [m for m in messages if m["kind"] == "question" and m["question_state"] == "open" and m["child_run_id"] == child.get("child_run_id")]
        self.query_one("#child-reply", Button).disabled = not active or not questions
        if child:
            lines = [child_label(child), "", child.get("task", "")]
            if child.get("error"):
                lines.extend(["", child["error"]])
            for message in messages:
                state = message.get("question_state") or message.get("delivery", "")
                lines.extend(["", f"{message['kind']} #{message['id']} · {state}", message["content"]])
            content = "\n".join(lines)
        else:
            recovery = self.chat.get_session_recovery(self.sid)
            content = (recovery.get("question", "") + "\n\n" + recovery.get("answer", "")) if recovery else "当前会话没有子任务。\n执行记录可查看保存状态、检查点和任务恢复信息。"
        detail = self.query_one("#child-detail", TextArea)
        if detail.text != content:
            detail.load_text(content)

    @on(Button.Pressed, "#child-reply, #child-send")
    @work(group="child-message", exclusive=True)
    async def message_child(self, event):
        child = dict(self.children_by_id[self.selected_id])
        question = None
        if event.button.id == "child-reply":
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

    @on(Button.Pressed, "#child-stop")
    def stop_child(self):
        accepted = self.chat.cancel_subagent(self.sid, self.selected_id)
        self.app.notify("已请求停止子任务" if accepted else "子任务已经结束")
        self.refresh_data()

    @on(Button.Pressed, "#task-record")
    @work(group="task-record")
    async def show_record(self):
        recovery = self.chat.get_session_recovery(self.sid)
        result = await asyncio.to_thread(self.terminal.run, "/subagents", f"show {self.selected_id}", self.sid) if self.selected_id else None
        version = self.chat.session_version(self.sid)
        record = {"session_id": self.sid, "recovery": recovery, "checkpoint": version.status() if version else None,
                  "child": result.value if result else None}
        self.app.push_screen(RecordScreen("执行记录", json.dumps(record, ensure_ascii=False, indent=2, default=str)))

    def action_close(self):
        self.dismiss(None)
