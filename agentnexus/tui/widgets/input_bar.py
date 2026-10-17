"""Input bar — styled prompt + text input + send button."""

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Button, Input, Static


class TerminalInput(Input):
    """Route multiline paste to the draft editor before it can be submitted."""

    async def _on_paste(self, event: events.Paste):
        if "\n" in event.text or "\r" in event.text:
            start, end = sorted(self.selection)
            text = event.text.replace("\r\n", "\n").replace("\r", "\n")
            self.post_message(InputBar.EditorRequested(self.value[:start] + text + self.value[end:]))
            event.stop()
        else:
            await super()._on_paste(event)


class InputBar(Widget):
    """Bottom input area with prompt, text input, and send button."""

    DEFAULT_CSS = """
    InputBar {
        height: auto;
    }
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.compose_draft_text = ""

    class AppSubmit(Message):
        def __init__(self, text: str):
            super().__init__()
            self.text = text

    class AppInputChanged(Message):
        def __init__(self, text: str):
            super().__init__()
            self.text = text

    class EditorRequested(Message):
        def __init__(self, text: str):
            super().__init__()
            self.text = text

    def compose(self) -> ComposeResult:
        self._inp = TerminalInput(
            placeholder="输入任务，或按 Ctrl+P 查找操作",
            id="chat-input",
        )
        yield Static("", id="composer-meta")
        with Horizontal(id="input-row"):
            yield Static("›", id="input-prompt")
            yield self._inp
        yield Static("Enter 发送 · F4 多行 · Ctrl+P 操作 · F2 会话 · F3 任务", id="composer-hints")

    def update_state(self, *, model, thinking, plan, attachments, draft, queued):
        self.compose_draft_text = draft
        meta = Text(model, style="bold")
        meta.append(f" · 思考 {thinking}", "dim")
        if plan:
            meta.append(" · 计划模式")
        if attachments:
            meta.append(" · 附件 " + ", ".join(item["name"] for item in attachments))
        if draft:
            meta.append(f" · 草稿 {len(draft.splitlines())} 行")
        self.query_one("#composer-meta", Static).update(meta)
        send = "Enter 排队 · Ctrl+C 停止" if queued else "Enter 发送"
        self.query_one("#composer-hints", Static).update(f"{send} · F4 多行 · Ctrl+P 操作 · F2 会话 · F3 任务")

    def on_mount(self):
        self.call_after_refresh(self._focus_input)

    def _focus_input(self):
        try:
            self.query_one("#chat-input", Input).focus()
        except Exception:
            pass

    def on_input_submitted(self, event: Input.Submitted):
        text = event.value.strip() or self.compose_draft_text
        if text.strip():
            self.post_message(self.AppSubmit(text))

    def on_input_changed(self, event: Input.Changed):
        self.post_message(self.AppInputChanged(event.value))

    def on_button_pressed(self, event: Button.Pressed):
        if self._inp.value.strip():
            self.post_message(self.AppSubmit(self._inp.value.strip()))
