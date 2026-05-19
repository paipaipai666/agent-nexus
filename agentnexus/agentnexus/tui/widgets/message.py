"""Chat message widget — role-colored left border, code-block rendering."""

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Label, Static


class ChatMessage(Widget):
    """A single chat message with colored left border."""

    def __init__(self, role: str, content: str, **kwargs):
        super().__init__(classes=role, **kwargs)
        self.content = content

    def compose(self) -> ComposeResult:
        yield Static(self.content, id="msg-content")

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
                w = Static(part, classes="msg-text")
                w.styles.height = part.count("\n") + 1
                self.mount(w)
            else:
                code = part
                lines = part.split("\n", 1)
                if len(lines) > 1:
                    code = lines[1]
                code = code.rstrip("\n")
                w = Static(code, classes="msg-code")
                w.styles.height = code.count("\n") + 1
                self.mount(w)


class ToolCall(Widget):
    """Inline tool call display — warm orange border, tool name + result."""

    def __init__(self, tool_name: str, result: str = "", duration_ms: float = 0, **kwargs):
        super().__init__(**kwargs)
        self.tool_name = tool_name
        self.result = result
        self.duration_ms = duration_ms

    def compose(self) -> ComposeResult:
        yield Label(f"⚙ {self.tool_name}", id="tool-name")
        yield Static(self.result or "", id="tool-result")
        if self.duration_ms:
            yield Label(f"→ {self.duration_ms:.0f}ms", id="tool-meta")

    def on_mount(self):
        lines = (self.result or "").count("\n") + 1
        self.query_one("#tool-result", Static).styles.height = lines

    def update_result(self, result: str, duration_ms: float = 0):
        self.result = result
        self.duration_ms = duration_ms
        widget = self.query_one("#tool-result", Static)
        widget.update(result or "")
        if result:
            lines = result.count("\n") + 1
            widget.styles.height = lines
        if duration_ms:
            try:
                meta = self.query_one("#tool-meta", Label)
                meta.update(f"→ {duration_ms:.0f}ms")
            except Exception:
                self.mount(Label(f"→ {duration_ms:.0f}ms", id="tool-meta"))
