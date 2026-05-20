"""Chat message widget — role-colored left border, code-block rendering."""

from rich.text import Text
from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Label, Static


def _safe(text: str) -> Text:
    """Wrap string in Rich Text to prevent markup parsing errors from [ ] chars."""
    return Text(text or "")


class ChatMessage(Widget):
    """A single chat message with colored left border."""

    def __init__(self, role: str, content: str, *, markup: bool = False, **kwargs):
        super().__init__(classes=role, **kwargs)
        self.content = content
        self._rich_markup = markup

    def compose(self) -> ComposeResult:
        if self._rich_markup:
            yield Static(self.content, id="msg-content")
        else:
            yield Static(_safe(self.content), id="msg-content")

    def on_mount(self):
        if "```" in self.content:
            self._render_code_blocks()

    def _render_code_blocks(self):
        """Replace generic content with segmented text/code rendering."""
        content_widget = self.query_one("#msg-content", Static)
        content_widget.remove()

        parts = self.content.split("```")
        for i, part in enumerate(parts):
            if not part.strip():
                continue
            if i % 2 == 0:
                w = Static(_safe(part), classes="msg-text")
                w.styles.height = "auto"
                self.mount(w)
            else:
                code = part
                lines = part.split("\n", 1)
                if len(lines) > 1:
                    code = lines[1]
                code = code.rstrip("\n")
                w = Static(_safe(code), classes="msg-code")
                w.styles.height = "auto"
                self.mount(w)


class ToolCall(Widget):
    """Inline tool call display — warm orange border, tool name + result."""

    def __init__(self, tool_name: str, result: str = "", duration_ms: float = 0, **kwargs):
        super().__init__(**kwargs)
        self.tool_name = tool_name
        self.result = result
        self.duration_ms = duration_ms

    def compose(self) -> ComposeResult:
        yield Label(f"{self.tool_name}", id="tool-name")
        yield Static(_safe(str(self.result) if self.result else ""), id="tool-result")
        if self.duration_ms:
            yield Label(f"→ {self.duration_ms:.0f}ms", id="tool-meta")

    def on_mount(self):
        self.query_one("#tool-result", Static).styles.height = "auto"

    def update_result(self, result: str, duration_ms: float = 0):
        self.result = result
        self.duration_ms = duration_ms
        widget = self.query_one("#tool-result", Static)
        widget.update(_safe(result or ""))
        widget.styles.height = "auto"
        if duration_ms:
            try:
                meta = self.query_one("#tool-meta", Label)
                meta.update(f"→ {duration_ms:.0f}ms")
            except Exception:
                self.mount(Label(f"→ {duration_ms:.0f}ms", id="tool-meta"))
