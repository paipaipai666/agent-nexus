"""Keyboard-first action selection, multiline editing and record inspection."""

from dataclasses import dataclass

from rich.text import Text
from textual import on
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Label, OptionList, TextArea
from textual.widgets.option_list import Option


@dataclass(frozen=True)
class Choice:
    """One selectable operation or session with a stable value."""

    label: str
    value: str
    description: str = ""
    execute: bool = True


class ChoiceScreen(ModalScreen[Choice | None]):
    """Filter and select a small set of terminal actions or saved sessions."""

    BINDINGS = [("escape", "cancel", "返回"), ("down", "next", "下一项"), ("up", "previous", "上一项")]
    DEFAULT_CSS = """
    ChoiceScreen { align: center top; padding-top: 2; }
    ChoiceScreen > Vertical { width: 90%; max-width: 100; height: 80%; background: $surface; border: solid $primary; padding: 0 1; }
    ChoiceScreen Label { height: 1; color: $text-muted; }
    ChoiceScreen Input { height: 3; background: transparent; }
    ChoiceScreen OptionList { height: 1fr; border: none; background: transparent; }
    """

    def __init__(self, title, choices):
        super().__init__()
        self.heading = title
        self.choices = list(choices)
        self.visible_choices = list(choices)

    def compose(self):
        with Vertical():
            yield Label(self.heading)
            yield Input(placeholder="输入名称、命令或工作区进行筛选", id="palette-search")
            yield OptionList(id="palette-options", compact=True)
            yield Label("↑↓ 选择 · Enter 确认 · Esc 返回")

    def on_mount(self):
        self._filter("")
        self.query_one(Input).focus()

    def _filter(self, query):
        words = query.casefold().split()
        self.visible_choices = [choice for choice in self.choices if all(word in f"{choice.label} {choice.value} {choice.description}".casefold() for word in words)]
        options = self.query_one(OptionList)
        options.clear_options()
        for index, choice in enumerate(self.visible_choices):
            text = Text(choice.label)
            if choice.description:
                text.append("\n" + choice.description, "dim")
            options.add_option(Option(text, id=str(index)))
        options.highlighted = 0 if self.visible_choices else None

    @on(Input.Changed)
    def filter_choices(self, event):
        self._filter(event.value)

    @on(Input.Submitted)
    def submit_choice(self):
        selected = self.query_one(OptionList).highlighted
        if selected is not None:
            self.dismiss(self.visible_choices[selected])

    @on(OptionList.OptionSelected)
    def choose_option(self, event):
        self.dismiss(self.visible_choices[event.option_index])

    def action_next(self):
        self.query_one(OptionList).action_cursor_down()

    def action_previous(self):
        self.query_one(OptionList).action_cursor_up()

    def action_cancel(self):
        self.dismiss(None)


class MessageEditor(ModalScreen[tuple[str, str]]):
    """Edit a multiline draft without sending on ordinary Enter or paste."""

    BINDINGS = [("f4", "send", "发送"), ("escape", "save", "保留草稿")]
    DEFAULT_CSS = """
    MessageEditor { align: center middle; }
    MessageEditor > Vertical { width: 94%; height: 85%; background: $surface; border: solid $primary; padding: 0 1; }
    MessageEditor Label { height: 1; color: $text-muted; }
    MessageEditor TextArea { height: 1fr; border: none; background: transparent; }
    """

    def __init__(self, text, title="多行消息"):
        super().__init__()
        self.text, self.heading = text, title

    def compose(self):
        with Vertical():
            yield Label(self.heading)
            yield TextArea(self.text, id="message-editor", soft_wrap=True)
            yield Label("Enter 换行 · F4 发送 · Esc 保留草稿并返回")

    def on_mount(self):
        self.query_one(TextArea).focus()

    def action_send(self):
        text = self.query_one(TextArea).text
        if text.strip():
            self.dismiss(("send", text))

    def action_save(self):
        self.dismiss(("save", self.query_one(TextArea).text))


class RecordScreen(ModalScreen[None]):
    """Inspect actual task evidence in a scrollable, selectable text buffer."""

    BINDINGS = [("escape", "close", "返回")]
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
