"""Input bar — >>> prompt + text input + send button."""

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Button, Input, Label


class InputBar(Widget):
    """Bottom input area. Sets focus to Input child automatically."""

    def compose(self) -> ComposeResult:
        yield Label(">>>", id="input-prompt")
        inp = Input(placeholder="输入消息... /help 帮助",
                    id="chat-input")
        inp.can_focus = True
        yield inp
        yield Button("发送", id="send-btn", variant="primary")

    def on_mount(self):
        """Auto-focus the input widget."""
        self.call_after_refresh(self._focus_input)

    def _focus_input(self):
        try:
            self.query_one("#chat-input", Input).focus()
        except Exception:
            pass
