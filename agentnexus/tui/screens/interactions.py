"""Keyboard-first action selection, multiline editing and record inspection."""

from dataclasses import dataclass
from typing import TypeVar

from rich.text import Text
from textual import on
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Label, OptionList, Static, TextArea
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
        self.app.push_screen(RecordScreen("当前界面的键盘操作", self.help_text()),
                             lambda _: focused.focus() if focused and focused.is_mounted else None)

    def help_text(self):
        """Describe the current interaction without requiring a separate manual."""
        return describe_actions(self.action_states())


@dataclass(frozen=True)
class Choice:
    """One selectable operation or session with a stable value."""

    label: str
    value: str
    description: str = ""
    execute: bool = True
    disabled_reason: str = ""


class ChoiceScreen(ActionScreen[Choice | None]):
    """Filter and select a small set of terminal actions or saved sessions."""

    ACTIONS = (TerminalAction("cancel", "返回", "escape", "cancel", key_display="Esc"),
               TerminalAction("next", "下一项", "down", "next", key_display="↓"),
               TerminalAction("previous", "上一项", "up", "previous", key_display="↑"),
               TerminalAction("select", "确认", "enter", "submit_choice"))
    BINDINGS = [action.binding() for action in ACTIONS]
    DEFAULT_CSS = """
    ChoiceScreen { align: center bottom; background: transparent; }
    ChoiceScreen > Vertical { width: 100%; max-width: 100; height: 12; max-height: 70%; background: $background; border: none; border-top: solid $foreground; padding: 0 2; }
    ChoiceScreen Label { height: 1; color: $text-muted; }
    ChoiceScreen #palette-search { height: 1; padding: 0; border: none; background: $background; }
    ChoiceScreen Input .input--placeholder { color: $text-muted; }
    ChoiceScreen OptionList { height: 1fr; border: none; background: transparent; }
    ChoiceScreen #palette-options > .option-list--option-highlighted { color: $foreground; background: transparent; text-style: bold; }
    ChoiceScreen #picker-detail { height: 1; color: $text-muted; text-wrap: nowrap; text-overflow: ellipsis; }
    ChoiceScreen ContextFooter { height: 1; color: $text-muted; }
    """

    def __init__(self, title, choices, *, selected_value=None, query=""):
        super().__init__()
        self.heading = title
        self.choices = list(choices)
        self.visible_choices = list(choices)
        self.selected_value = selected_value
        self.search_text = query
        self._marked_index = None

    def compose(self):
        with Vertical(id="picker-body"):
            yield Label(Text(self.heading))
            yield Input(self.search_text, placeholder="输入名称、命令或工作区进行筛选", id="palette-search")
            yield OptionList(id="palette-options", compact=True)
            yield Static("", id="picker-detail")
            yield ContextFooter(id="picker-actions")

    def on_mount(self):
        self._filter(self.search_text)
        self.query_one(Input).focus()
        states = self.action_states()
        order = ["previous", "next", "select", "cancel", "help"]
        self.query_one(ContextFooter).show_actions([next(state for state in states if state.action.id == key) for key in order])

    def _filter(self, query):
        self.search_text = query
        words = query.casefold().split()
        self.visible_choices = [choice for choice in self.choices if all(word in f"{choice.label} {choice.value} {choice.description}".casefold() for word in words)]
        options = self.query_one(OptionList)
        options.clear_options()
        self._marked_index = None
        for index, choice in enumerate(self.visible_choices):
            text = Text("  " + choice.label)
            options.add_option(Option(text, id=str(index), disabled=bool(choice.disabled_reason)))
        enabled = [i for i, choice in enumerate(self.visible_choices) if not choice.disabled_reason]
        selected = next((i for i in enabled if self.visible_choices[i].value == self.selected_value), enabled[0] if enabled else None)
        options.highlighted = selected if self.visible_choices else None
        if not self.visible_choices:
            options.add_option(Option(Text("没有匹配操作，修改搜索词或按 Esc 返回", style="dim"), disabled=True))
        self.query_one("#picker-body").styles.height = min(13, max(6, len(self.visible_choices) + 5))
        self._show_detail()

    def _show_detail(self):
        options = self.query_one(OptionList)
        selected = options.highlighted
        for index in {self._marked_index, selected}:
            if index is not None and index < len(self.visible_choices):
                choice = self.visible_choices[index]
                options.replace_option_prompt(str(index), Text(("› " if index == selected else "  ") + choice.label))
        self._marked_index = selected
        choice = self.visible_choices[selected] if selected is not None and selected < len(self.visible_choices) else self.visible_choices[0] if self.visible_choices else None
        text = choice.disabled_reason or choice.description or choice.value if choice else "没有匹配操作"
        self.query_one("#picker-detail", Static).update(Text(text))

    @on(Input.Changed)
    def filter_choices(self, event):
        self._filter(event.value)

    def action_submit_choice(self):
        selected = self.query_one(OptionList).highlighted
        if selected is not None and selected < len(self.visible_choices) and not self.visible_choices[selected].disabled_reason:
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
        selected = self.query_one(OptionList).highlighted
        if selected is not None and selected < len(self.visible_choices):
            self.selected_value = self.visible_choices[selected].value
            self._show_detail()

    def action_cancel(self):
        self.dismiss(None)

    def help_text(self):
        selected = self.query_one(OptionList).highlighted
        choice = self.visible_choices[selected] if selected is not None and selected < len(self.visible_choices) else None
        detail = f"{choice.label}\n{choice.value}\n{choice.disabled_reason or choice.description}\n\n" if choice else ""
        return detail + super().help_text()


class ParameterScreen(ActionScreen[str | None]):
    """Collect one operation parameter without overwriting the task composer."""

    ACTIONS = (TerminalAction("cancel", "返回", "escape", "cancel", key_display="Esc"),
               TerminalAction("submit", "确认", "enter", "submit"))
    BINDINGS = [action.binding() for action in ACTIONS]
    DEFAULT_CSS = """
    ParameterScreen { align: center bottom; background: transparent; }
    ParameterScreen > Vertical { width: 100%; max-width: 100; height: auto; padding: 0 2; background: $background; border: none; border-top: solid $foreground; }
    ParameterScreen Label { height: auto; }
    ParameterScreen #parameter-input { height: 1; border: none; padding: 0; background: $background; }
    ParameterScreen #parameter-error { color: $error; height: auto; }
    ParameterScreen ContextFooter { height: 1; color: $text-muted; }
    """

    def __init__(self, title, prompt, *, allow_empty=False):
        super().__init__()
        self.heading, self.prompt = title, prompt
        self.allow_empty = allow_empty

    def compose(self):
        with Vertical():
            yield Label(Text(self.heading))
            yield Label(Text(self.prompt))
            yield Input(id="parameter-input")
            error = Static("", id="parameter-error")
            error.display = False
            yield error
            yield ContextFooter(id="parameter-actions")

    def on_mount(self):
        self.query_one(Input).focus()
        self.query_one(ContextFooter).show_actions(self.action_states())

    def action_submit(self):
        value = self.query_one(Input).value.strip()
        if value or self.allow_empty:
            self.dismiss(value)
        else:
            self.query_one("#parameter-error", Static).update("请输入内容后确认")
            self.query_one("#parameter-error").display = True

    def action_cancel(self):
        self.dismiss(None)


class MessageEditor(ActionScreen[tuple[str, str]]):
    """Edit a multiline draft without sending on ordinary Enter or paste."""

    ACTIONS = (TerminalAction("send", "发送", "ctrl+s", "send"),
               TerminalAction("save", "返回", "escape", "save", "保留未发送的草稿并返回", key_display="Esc"))
    BINDINGS = [action.binding() for action in ACTIONS]
    DEFAULT_CSS = """
    MessageEditor { align: center middle; }
    MessageEditor > Vertical { width: 96%; height: 85%; background: $surface; border: none; border-top: solid $foreground; padding: 0 1; }
    MessageEditor Label { height: 1; color: $text-muted; }
    MessageEditor #message-editor { height: 1fr; padding: 0; border: none; background: transparent; }
    MessageEditor ContextFooter { height: 1; color: $text-muted; }
    """

    def __init__(self, text, title="多行消息"):
        super().__init__()
        self.text, self.heading = text, title

    def compose(self):
        with Vertical():
            yield Label(Text(self.heading))
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
    RecordScreen { align: center middle; background: $background; }
    RecordScreen > Vertical { width: 100%; height: 100%; background: $background; border: none; padding: 0 2; }
    RecordScreen Label { height: 1; color: $text-muted; }
    RecordScreen #record-content { height: 1fr; padding: 0; border: none; background: transparent; }
    RecordScreen ContextFooter { height: 1; color: $text-muted; }
    """

    def __init__(self, title, text):
        super().__init__()
        self.heading, self.text = title, text

    def compose(self):
        with Vertical():
            yield Label(Text(self.heading))
            yield TextArea(self.text, id="record-content", read_only=True, soft_wrap=True)
            yield ContextFooter(id="record-actions")

    def on_mount(self):
        self.query_one(TextArea).focus()
        self.query_one(ContextFooter).show_actions(self.action_states(), prefix="↑↓ / PgUp / PgDn 浏览 · Ctrl+C 复制")

    def help_text(self):
        return "方向键与 PageUp/PageDown 浏览；Shift+方向键选择文字，Ctrl+C 复制。\n\n" + super().help_text()

    def action_close(self):
        self.dismiss(None)
