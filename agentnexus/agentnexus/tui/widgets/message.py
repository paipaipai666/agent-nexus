"""Chat message widget — role-colored left border, clean text flow."""

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Label, Static


class ChatMessage(Widget):
    """A single chat message with colored left border and role label."""

    def __init__(self, role: str, content: str, display_name: str = "", **kwargs):
        super().__init__(classes=role, **kwargs)
        self.role = role
        self.content = content
        self.display_name = display_name or ("You" if role == "user" else "AgentNexus")

    def compose(self) -> ComposeResult:
        yield Label(self.display_name, id="msg-role", classes=self.role)
        yield Static(self.content, id="msg-content")

    def on_mount(self):
        # Conservative upper bound: Rich consumes markup (backticks etc.), never adds lines.
        # Extra blank lines are harmless; truncated code blocks are not.
        lines = self.content.count("\n") + 1
        self.query_one("#msg-content", Static).styles.height = lines


class ToolCall(Widget):
    """Inline tool call display — warm orange border, tool name + result."""

    def __init__(self, tool_name: str, result: str = "", duration_ms: float = 0, **kwargs):
        super().__init__(**kwargs)
        self.tool_name = tool_name
        self.result = result
        self.duration_ms = duration_ms

    def compose(self) -> ComposeResult:
        yield Label(f"⚙ {self.tool_name}", id="tool-name")
        if self.result:
            yield Static(self.result, id="tool-result")
        if self.duration_ms:
            yield Label(f"→ {self.duration_ms:.0f}ms", id="tool-meta")

    def on_mount(self):
        if self.result:
            lines = self.result.count("\n") + 1
            self.query_one("#tool-result", Static).styles.height = lines
