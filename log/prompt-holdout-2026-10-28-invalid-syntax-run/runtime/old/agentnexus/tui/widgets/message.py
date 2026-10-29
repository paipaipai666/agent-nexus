"""Conversation messages, model reasoning and inspectable tool results."""

from rich.markdown import Markdown
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Label, Static

from agentnexus.tui.activity import tool_label


def _safe(text: str) -> Text:
    """Wrap string in Rich Text to prevent markup parsing errors from [ ] chars."""
    return Text(text or "")


def render_diff_with_colors(diff_text: str) -> str:
    """Keep unified diff signs readable with the terminal's basic colors."""
    lines = diff_text.split('\n')
    colored_lines = []

    for line in lines:
        escaped_line = line.replace('[', '\\[').replace(']', '\\]')

        if line.startswith('---') or line.startswith('+++'):
            colored_lines.append(f"[bold]{escaped_line}[/bold]")
        elif line.startswith('-'):
            colored_lines.append(f"[red]{escaped_line}[/red]")
        elif line.startswith('+'):
            colored_lines.append(f"[green]{escaped_line}[/green]")
        elif line.startswith('@@'):
            colored_lines.append(f"[cyan]{escaped_line}[/cyan]")
        else:
            colored_lines.append(escaped_line)

    return '\n'.join(colored_lines)


class ChatMessage(Widget):
    """Message content with role-specific styling."""

    def __init__(self, role: str, content: str, *, markup: bool = False, **kwargs):
        super().__init__(classes=role, **kwargs)
        self.content = content
        self._rich_markup = markup

    def compose(self) -> ComposeResult:
        if self._rich_markup:
            yield Static(self.content, id="msg-content")
        elif "assistant" in self.classes:
            yield Static(Markdown(self.content, code_theme="ansi_dark"), id="msg-content")
        elif "user" in self.classes:
            with Horizontal(classes="request-line"):
                yield Static("›", classes="request-marker")
                yield Static(_safe(self.content), id="msg-content")
        else:
            yield Static(_safe(self.content), id="msg-content")

    def on_mount(self):
        if "assistant" in self.classes and "```" in self.content:
            self._render_code_blocks()

    def _render_code_blocks(self):
        """Render Markdown without replacing the streaming message target."""
        self.query_one("#msg-content", Static).update(Markdown(self.content, code_theme="ansi_dark"))

    def attach_reaction(self, emoji: str, comment: str = ""):
        """Mount a small emoji reaction row under this message (user bubbles).

        Used by the express_reaction entertainment tool — renders as a subtle
        footnote instead of a tool card.
        """
        if not emoji:
            return
        quip = f" {comment}" if comment else ""
        self.mount(Static(_safe(f"{emoji}{quip}"), classes="msg-reaction"))


class ReasoningBlock(Widget, can_focus=True):
    """Model-provided reasoning, visible while streaming and readable afterward."""

    BINDINGS = [("enter", "toggle_details", "折叠思考"), ("space", "toggle_details", "折叠思考"), ("d", "full_output", "思考全文")]

    class Updated(Message):
        """Signal when a throttled reasoning update changes transcript geometry."""
    DEFAULT_CSS = """
    ReasoningBlock { height: auto; max-width: 100; margin: 0 0 1 0; padding: 0; border-left: blank $foreground; background: transparent; }
    ReasoningBlock:focus { border-left: solid $primary; }
    .reasoning-heading { height: 1; width: 100%; color: $text-muted; text-wrap: nowrap; text-overflow: ellipsis; }
    ReasoningBlock.reasoning-active .reasoning-heading { color: $primary; }
    .reasoning-content { height: auto; padding-left: 2; color: $foreground; }
    """

    def __init__(self, content="", *, active=False, **kwargs):
        super().__init__(**kwargs)
        self.content = content
        self.active = active
        self.expanded = True
        self._update_pending = False
        self.set_class(active, "reasoning-active")

    def _heading(self):
        marker = "▾" if self.expanded else "▸"
        status = "思考中" if self.active else "思考"
        return Text(f"{marker} {status}")

    def compose(self):
        yield Label(self._heading(), classes="reasoning-heading")
        yield Static(Text(self.content), classes="reasoning-content")

    def append_token(self, token):
        self.content += token
        if not self._update_pending:
            self._update_pending = True
            self.set_timer(0.05, self._flush)

    def _flush(self):
        self._update_pending = False
        if self.is_mounted:
            self.set_class(self.active, "reasoning-active")
            self.query_one(".reasoning-heading", Label).update(self._heading())
            self.query_one(".reasoning-content", Static).update(Text(self.content))
            self.post_message(self.Updated())

    def finish(self):
        self.active = False
        self._flush()

    def action_toggle_details(self):
        self.expanded = not self.expanded
        self.query_one(".reasoning-content").display = self.expanded
        self.query_one(".reasoning-heading", Label).update(self._heading())

    def on_click(self, event):
        if event.widget.has_class("reasoning-heading"):
            self.focus()
            self.action_toggle_details()

    def action_full_output(self):
        from agentnexus.tui.screens.interactions import RecordScreen

        self.app.push_screen(RecordScreen("模型思考 · 全文", self.content))


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
        return Text(f"{marker} {tool_label(self.tool_name)}{detail}{state}{duration}")

    def compose(self) -> ComposeResult:
        yield Label(self._header(), id="tool-name")
        result = Static(_safe(str(self.result) if self.result else ""), id="tool-result")
        result.display = self.expanded
        yield result

    def on_mount(self):
        self.query_one("#tool-result", Static).styles.height = "auto"

    def action_toggle_details(self):
        self.expanded = not self.expanded
        self.query_one("#tool-result").display = self.expanded
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
