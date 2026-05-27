"""Chat message widget — role-colored left border, code-block rendering."""

from rich.text import Text
from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Label, Static


def _safe(text: str) -> Text:
    """Wrap string in Rich Text to prevent markup parsing errors from [ ] chars."""
    return Text(text or "")


def render_diff_with_colors(diff_text: str) -> str:
    """Render unified diff with Rich markup background colors for TUI display.

    - Lines starting with '-' (removed) have red background
    - Lines starting with '+' (added) have green background
    - Lines starting with '@@' (hunk headers) have cyan background
    - Other lines are kept as-is
    """
    lines = diff_text.split('\n')
    colored_lines = []

    for line in lines:
        # Escape Rich markup special characters in the content
        escaped_line = line.replace('[', '\\[').replace(']', '\\]')

        if line.startswith('---') or line.startswith('+++'):
            # File headers - bold
            colored_lines.append(f"[bold]{escaped_line}[/bold]")
        elif line.startswith('-'):
            # Removed lines - red background
            colored_lines.append(f"[white on red]{escaped_line}[/white on red]")
        elif line.startswith('+'):
            # Added lines - green background
            colored_lines.append(f"[white on green]{escaped_line}[/white on green]")
        elif line.startswith('@@'):
            # Hunk headers - cyan background
            colored_lines.append(f"[white on dark_cyan]{escaped_line}[/white on dark_cyan]")
        else:
            # Context lines - default
            colored_lines.append(escaped_line)

    return '\n'.join(colored_lines)


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

    def update_result(self, result: str, duration_ms: float = 0, *, markup: bool = False):
        self.result = result
        self.duration_ms = duration_ms
        widget = self.query_one("#tool-result", Static)
        if markup:
            widget.update(result or "")
        else:
            widget.update(_safe(result or ""))
        widget.styles.height = "auto"
        if duration_ms:
            try:
                meta = self.query_one("#tool-meta", Label)
                meta.update(f"→ {duration_ms:.0f}ms")
            except Exception:
                self.mount(Label(f"→ {duration_ms:.0f}ms", id="tool-meta"))
