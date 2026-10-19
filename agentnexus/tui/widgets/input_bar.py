"""Inline terminal composition with explicit send and multiline modes."""

from math import ceil

from rich.cells import cell_len
from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Static, TextArea

from agentnexus.tui.actions import ContextFooter
from agentnexus.tui.widgets.activity import ActivityStatus


class Composer(TextArea):
    """Keep typed and pasted messages in one buffer with predictable submission."""

    BINDINGS = [
        Binding("enter", "enter", "发送 / 换行", priority=True),
        Binding("ctrl+s", "submit", "发送", priority=True),
        Binding("shift+enter,alt+enter,ctrl+j", "newline", "换行", priority=True),
        Binding("up", "up", "上一项", priority=True),
        Binding("down", "down", "下一项", priority=True),
    ]

    class Submitted(Message):
        """Submit the complete buffer exactly once."""

        def __init__(self, text):
            super().__init__()
            self.text = text

    def __init__(self, **kwargs):
        super().__init__(soft_wrap=True, show_line_numbers=False, tab_behavior="focus", **kwargs)
        self.multiline = False
        self.suggestions_active = False

    class SuggestionRequested(Message):
        """Navigate or accept a visible command candidate without leaving input."""

        def __init__(self, action):
            super().__init__()
            self.action = action

    @property
    def value(self):
        """Expose the complete pending message to session draft persistence."""
        return self.text

    @value.setter
    def value(self, text):
        self.load_text(text)
        self.move_cursor(self.document.end)
        self.multiline = "\n" in text

    def action_enter(self):
        if self.suggestions_active and not self.multiline:
            self.post_message(self.SuggestionRequested("execute"))
        elif self.multiline:
            self.action_newline()
        else:
            self.action_submit()

    def action_up(self):
        if self.suggestions_active:
            self.post_message(self.SuggestionRequested("previous"))
        else:
            self.action_cursor_up()

    def action_down(self):
        if self.suggestions_active:
            self.post_message(self.SuggestionRequested("next"))
        else:
            self.action_cursor_down()

    def action_newline(self):
        self.multiline = True
        self.insert("\n")

    def action_submit(self):
        if self.text.strip():
            self.post_message(self.Submitted(self.text))

    def _on_paste(self, event: events.Paste):
        if "\n" in event.text or "\r" in event.text:
            self.multiline = True
        event.stop()


class InputBar(Widget):
    """Task buffer, current configuration and terminal-safe action shortcuts."""

    def __init__(self, *args, hud=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._hud = hud
        self._queued = False

    class AppSubmit(Message):
        def __init__(self, text: str):
            super().__init__()
            self.text = text

    class AppInputChanged(Message):
        def __init__(self, text: str):
            super().__init__()
            self.text = text

    def compose(self) -> ComposeResult:
        yield Static("", id="composer-meta")
        with Horizontal(id="input-row"):
            yield Static("›", id="input-prompt")
            self._inp = Composer(id="chat-input", placeholder="描述任务，或按 Ctrl+P 选择操作")
            yield self._inp
        with Horizontal(id="composer-footer"):
            yield ActivityStatus(id="agent-activity")
            if self._hud is not None:
                yield self._hud
            yield ContextFooter("", id="composer-hints")

    def update_state(self, *, model, thinking, plan, attachments, draft, queued):
        self._queued = queued
        if draft and not self._inp.text:
            self._inp.value = draft
        meta = Text()
        if attachments:
            meta.append("附件 " + ", ".join(item["name"] for item in attachments))
        self.query_one("#composer-meta", Static).update(meta)
        self.query_one("#composer-meta").display = bool(attachments)
        if self._hud is not None:
            self._hud.update_configuration(thinking=thinking, plan=plan)
        self._update_hints()

    def toggle_multiline(self):
        self._inp.multiline = not self._inp.multiline
        self._update_size()
        self._update_hints()
        self._inp.focus()

    def fit_footer(self):
        """Give state and direct actions space before optional session metadata."""
        if not self.is_mounted:
            return
        width = self.query_one("#composer-footer").size.width
        if not width:
            return
        activity = self.query_one(ActivityStatus)
        hints = self.query_one(ContextFooter)
        status_width = min(cell_len(activity.activity.label()), max(8, width // 2))
        hint_width = min(hints.preferred_width, max(hints.minimum_width, width - status_width - 2))
        status_width = min(status_width, max(8, width - hint_width - 2))
        activity.styles.width = status_width
        if self._hud is not None:
            self._hud.fit_compact(max(0, width - status_width - hint_width - 4))

    def _update_hints(self):
        send = "排队" if self._queued else "发送"
        mode = f"Enter 换行 · Ctrl+S {send}" if self._inp.multiline else f"Enter {send}"
        action = "Ctrl+C 停止" if self._queued else "F4 单行" if self._inp.multiline else "F4 多行"
        self.query_one("#composer-hints", Static).update(f"{mode} · {action}")

    def _update_size(self):
        width = max(1, self._inp.size.width - 1)
        rows = sum(max(1, ceil(cell_len(line) / width)) for line in self._inp.text.split("\n"))
        self._inp.styles.height = min(6, max(1, rows))

    def on_resize(self):
        self._update_size()
        self.fit_footer()

    def on_mount(self):
        self._update_hints()
        self.call_after_refresh(self._inp.focus)

    def on_composer_submitted(self, event: Composer.Submitted):
        event.stop()
        self.post_message(self.AppSubmit(event.text))

    def on_text_area_changed(self, event: TextArea.Changed):
        event.stop()
        self._update_size()
        self._update_hints()
        self.post_message(self.AppInputChanged(event.text_area.text))

