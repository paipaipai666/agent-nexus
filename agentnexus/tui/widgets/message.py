"""Chat message widget — role-colored left border, code-block rendering."""

from rich.markdown import Markdown
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
        escaped_line = line.replace('[', '\\[').replace(']', '\\]')

        if line.startswith('---') or line.startswith('+++'):
            colored_lines.append(f"[bold]{escaped_line}[/bold]")
        elif line.startswith('-'):
            colored_lines.append(f"[white on red]{escaped_line}[/white on red]")
        elif line.startswith('+'):
            colored_lines.append(f"[white on green]{escaped_line}[/white on green]")
        elif line.startswith('@@'):
            colored_lines.append(f"[white on dark_cyan]{escaped_line}[/white on dark_cyan]")
        else:
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
        elif "assistant" in self.classes:
            yield Static(Markdown(self.content), id="msg-content")
        else:
            yield Static(_safe(self.content), id="msg-content")

    def on_mount(self):
        if "```" in self.content:
            self._render_code_blocks()

    def _render_code_blocks(self):
        """Render Markdown without replacing the streaming message target."""
        self.query_one("#msg-content", Static).update(Markdown(self.content))

    def attach_reaction(self, emoji: str, comment: str = ""):
        """Mount a small emoji reaction row under this message (user bubbles).

        Used by the express_reaction entertainment tool — renders as a subtle
        footnote instead of a tool card.
        """
        if not emoji:
            return
        quip = f" {comment}" if comment else ""
        self.mount(Static(_safe(f"{emoji}{quip}"), classes="msg-reaction"))


class ToolCall(Widget, can_focus=True):
    """A compact execution row with keyboard-accessible result details."""

    BINDINGS = [("enter", "toggle_details", "展开结果"), ("space", "toggle_details", "展开结果"), ("d", "full_output", "完整输出")]

    def __init__(self, tool_name: str, result: str = "", duration_ms: float = 0, *, raw_result: str | None = None, detail: str = "", **kwargs):
        super().__init__(**kwargs)
        self.tool_name = tool_name
        self.result = result
        self.duration_ms = duration_ms
        self.raw_result = result if raw_result is None else raw_result
        self.detail = detail
        self.expanded = False

    def _header(self):
        marker = "▾" if self.expanded else "▸"
        state = " · 执行中" if self.result == "executing..." else ""
        duration = f" · {self.duration_ms:.0f}ms" if self.duration_ms else ""
        detail = f" · {self.detail}" if self.detail else ""
        return Text(f"{marker} {self.tool_name}{detail}{state}{duration}")

    def compose(self) -> ComposeResult:
        yield Label(self._header(), id="tool-name")
        result = Static(_safe(str(self.result) if self.result else ""), id="tool-result")
        result.display = self.expanded
        yield result
        hint = Label("D 完整输出 · Enter 收起", id="tool-hint")
        hint.display = self.expanded
        yield hint

    def on_mount(self):
        self.query_one("#tool-result", Static).styles.height = "auto"

    def action_toggle_details(self):
        self.expanded = not self.expanded
        self.query_one("#tool-result").display = self.expanded
        self.query_one("#tool-hint").display = self.expanded
        self.query_one("#tool-name", Label).update(self._header())

    def on_click(self, event):
        if event.widget.id == "tool-name":
            self.focus()
            self.action_toggle_details()

    def action_full_output(self):
        from agentnexus.tui.screens.interactions import RecordScreen

        self.app.push_screen(RecordScreen(self.tool_name + " · 完整输出", self.raw_result))

    def update_result(self, result: str, duration_ms: float = 0, *, markup: bool = False, raw_result: str | None = None):
        self.result = result
        self.raw_result = result if raw_result is None else raw_result
        self.duration_ms = duration_ms
        self.query_one("#tool-name", Label).update(self._header())
        widget = self.query_one("#tool-result", Static)
        if markup:
            widget.update(result or "")
        else:
            widget.update(_safe(result or ""))
        widget.styles.height = "auto"
