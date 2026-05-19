"""Input bar — styled prompt + text input + send button."""

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Button, Input, Static, Label


class InputBar(Widget):
    """Bottom input area with prompt, text input, and send button."""

    DEFAULT_CSS = """
    InputBar {
        height: auto;
    }
    """

    class AppSubmit(Message):
        def __init__(self, text: str):
            super().__init__()
            self.text = text

    def compose(self) -> ComposeResult:
        self._inp = Input(
            placeholder="输入消息... (Enter 发送, /help 命令)",
            id="chat-input",
        )
        with Horizontal(id="input-row"):
            yield Static(">", id="input-prompt")
            yield self._inp

    def on_mount(self):
        self.call_after_refresh(self._focus_input)

    def _focus_input(self):
        try:
            self.query_one("#chat-input", Input).focus()
        except Exception:
            pass

    def on_input_submitted(self, event: Input.Submitted):
        if event.value.strip():
            self.post_message(self.AppSubmit(event.value.strip()))

    def on_button_pressed(self, event: Button.Pressed):
        if self._inp.value.strip():
            self.post_message(self.AppSubmit(self._inp.value.strip()))
