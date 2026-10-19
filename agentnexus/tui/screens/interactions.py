"""Keyboard-first action selection, multiline editing and record inspection."""

from dataclasses import dataclass
from typing import TypeVar

from rich.text import Text
from textual import on
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Label, OptionList, TextArea
from textual.widgets.option_list import Option

from agentnexus.tui.actions import ACTIONS as WORKSPACE_ACTIONS
from agentnexus.tui.actions import ActionState, ContextFooter, TerminalAction, describe_actions

ResultT = TypeVar("ResultT")


class ActionScreen(ModalScreen[ResultT]):
    """Use the same action definitions for modal keys, help and clickable hints."""

    ACTIONS: tuple[TerminalAction, ...] = ()
    BINDINGS = [WORKSPACE_ACTIONS["help"].binding()]

    def action_states(self):
        return [ActionState(action) for action in (*self.ACTIONS, WORKSPACE_ACTIONS["help"])]

    async def action_perform(self, action_id):
        state = next(state for state in self.action_states() if state.action.id == action_id)
        if state.disabled_reason:
            self.app.notify(state.disabled_reason)
        else:
            await self.run_action(state.action.handler)

    def action_context_help(self):
        focused = self.app.focused
        self.app.push_screen(RecordScreen("当前界面的键盘操作", describe_actions(self.action_states())),
                             lambda _: focused.focus() if focused and focused.is_mounted else None)


@dataclass(frozen=True)
class Choice:
    """One selectable operation or session with a stable value."""

    label: str
    value: str
    description: str = ""
    execute: bool = True


class ChoiceScreen(ActionScreen[Choice | None]):
    """Filter and select a small set of terminal actions or saved sessions."""

    ACTIONS = (TerminalAction("cancel", "返回", "escape", "cancel", key_display="Esc"),
               TerminalAction("next", "下一项", "down", "next", key_display="↓"),
               TerminalAction("previous", "上一项", "up", "previous", key_display="↑"),
               TerminalAction("select", "确认", "enter", "submit_choice"))
    BINDINGS = [action.binding() for action in ACTIONS]
    DEFAULT_CSS = """
    ChoiceScreen { align: center top; padding-top: 2; }
    ChoiceScreen > Vertical { width: 90%; max-width: 100; height: 80%; background: $surface; border: solid $primary; padding: 0 1; }
    ChoiceScreen Label { height: 1; color: $text-muted; }
    ChoiceScreen Input { height: 3; background: transparent; }
    ChoiceScreen Input .input--placeholder { color: $text-muted; }
    ChoiceScreen OptionList { height: 1fr; border: none; background: transparent; }
    """

    def __init__(self, title, choices, *, selected_value=None, query=""):
        super().__init__()
        self.heading = title
        self.choices = list(choices)
        self.visible_choices = list(choices)
        self.selected_value = selected_value
        self.search_text = query

    def compose(self):
        with Vertical():
            yield Label(self.heading)
            yield Input(self.search_text, placeholder="输入名称、命令或工作区进行筛选", id="palette-search")
            yield OptionList(id="palette-options", compact=True)
            yield Label("↑↓ 选择 · Enter 确认 · Esc 返回")

    def on_mount(self):
        self._filter(self.search_text)
        self.query_one(Input).focus()

    def _filter(self, query):
        self.search_text = query
        words = query.casefold().split()
        self.visible_choices = [choice for choice in self.choices if all(word in f"{choice.label} {choice.value} {choice.description}".casefold() for word in words)]
        options = self.query_one(OptionList)
        options.clear_options()
        for index, choice in enumerate(self.visible_choices):
            text = Text(choice.label)
            if choice.description:
                text.append("\n" + choice.description)
            options.add_option(Option(text, id=str(index)))
        selected = next((i for i, choice in enumerate(self.visible_choices) if choice.value == self.selected_value), 0)
        options.highlighted = selected if self.visible_choices else None
        if not self.visible_choices:
            options.add_option(Option(Text("没有匹配操作，修改搜索词或按 Esc 返回", style="dim"), disabled=True))

    @on(Input.Changed)
    def filter_choices(self, event):
        self._filter(event.value)

    def action_submit_choice(self):
        selected = self.query_one(OptionList).highlighted
        if selected is not None and selected < len(self.visible_choices):
            self.dismiss(self.visible_choices[selected])

    @on(OptionList.OptionSelected)
    def choose_option(self, event):
        self.query_one(OptionList).highlighted = event.option_index
        self.action_submit_choice()

    def action_next(self):
        self.query_one(OptionList).action_cursor_down()

    def action_previous(self):
        self.query_one(OptionList).action_cursor_up()

    @on(OptionList.OptionHighlighted)
    def remember_choice(self, event):
        if event.option_index < len(self.visible_choices):
            self.selected_value = self.visible_choices[event.option_index].value

    def action_cancel(self):
        self.dismiss(None)


class ParameterScreen(ActionScreen[str | None]):
    """Collect one operation parameter without overwriting the task composer."""

    ACTIONS = (TerminalAction("cancel", "返回", "escape", "cancel", key_display="Esc"),
               TerminalAction("submit", "确认", "enter", "submit"))
    BINDINGS = [action.binding() for action in ACTIONS]
    DEFAULT_CSS = """
    ParameterScreen { align: center middle; }
    ParameterScreen > Vertical { width: 90%; max-width: 90; height: auto; padding: 1 2; background: $surface; border: solid $primary; }
    ParameterScreen Label { height: auto; }
    ParameterScreen Input { background: transparent; }
    """

    def __init__(self, title, prompt, *, allow_empty=False):
        super().__init__()
        self.heading, self.prompt = title, prompt
        self.allow_empty = allow_empty

    def compose(self):
        with Vertical():
            yield Label(self.heading)
            yield Label(self.prompt)
            yield Input(id="parameter-input")
            yield Label("Enter 确认 · Esc 取消，原消息保持不变")

    def on_mount(self):
        self.query_one(Input).focus()

    def action_submit(self):
        value = self.query_one(Input).value.strip()
        if value or self.allow_empty:
            self.dismiss(value)

    def action_cancel(self):
        self.dismiss(None)


class MessageEditor(ActionScreen[tuple[str, str]]):
    """Edit a multiline draft without sending on ordinary Enter or paste."""

    ACTIONS = (TerminalAction("send", "发送", "ctrl+s", "send"),
               TerminalAction("save", "保留草稿并返回", "escape", "save", key_display="Esc"))
    BINDINGS = [action.binding() for action in ACTIONS]
    DEFAULT_CSS = """
    MessageEditor { align: center middle; }
    MessageEditor > Vertical { width: 94%; height: 85%; background: $surface; border: solid $primary; padding: 0 1; }
    MessageEditor Label { height: 1; color: $text-muted; }
    MessageEditor TextArea { height: 1fr; border: none; background: transparent; }
    MessageEditor ContextFooter { height: 1; color: $text-muted; }
    """

    def __init__(self, text, title="多行消息"):
        super().__init__()
        self.text, self.heading = text, title

    def compose(self):
        with Vertical():
            yield Label(self.heading)
            yield TextArea(self.text, id="message-editor", soft_wrap=True)
            yield ContextFooter(id="editor-actions")

    def on_mount(self):
        self.query_one(TextArea).focus()
        self._refresh_actions()

    def action_states(self):
        return [ActionState(state.action, "请输入消息" if state.action.id == "send" and not self.query_one(TextArea).text.strip() else "")
                for state in super().action_states()]

    def _refresh_actions(self):
        self.query_one(ContextFooter).show_actions(self.action_states(), prefix="Enter 换行")

    def on_text_area_changed(self):
        self._refresh_actions()

    def action_send(self):
        text = self.query_one(TextArea).text
        if text.strip():
            self.dismiss(("send", text))

    def action_save(self):
        self.dismiss(("save", self.query_one(TextArea).text))


class RecordScreen(ActionScreen[None]):
    """Inspect actual task evidence in a scrollable, selectable text buffer."""

    ACTIONS = (TerminalAction("close", "返回", "escape", "close", key_display="Esc"),)
    BINDINGS = [action.binding() for action in ACTIONS]
    DEFAULT_CSS = """
    RecordScreen { align: center middle; }
    RecordScreen > Vertical { width: 96%; height: 94%; background: $surface; border: solid $primary; padding: 0 1; }
    RecordScreen Label { height: 1; color: $text-muted; }
    RecordScreen TextArea { height: 1fr; border: none; background: transparent; }
    """

    def __init__(self, title, text):
        super().__init__()
        self.heading, self.text = title, text

    def compose(self):
        with Vertical():
            yield Label(self.heading)
            yield TextArea(self.text, id="record-content", read_only=True, soft_wrap=True)
            yield Label("方向键 / PageUp / PageDown 浏览 · Esc 返回")

    def action_close(self):
        self.dismiss(None)
