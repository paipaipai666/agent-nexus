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
from textual.widgets import Button, Static, TextArea

from agentnexus.tui.actions import CONTROL_ACTIONS, ContextFooter
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


class ConfigurationBar(Horizontal):
    """Navigate configuration controls without adding stops to the region cycle."""

    BINDINGS = [action.binding() for action in CONTROL_ACTIONS]

    async def action_perform(self, action_id):
        await self.screen.action_perform(action_id)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.selected_id = "choose-model"

    def focus_current(self):
        self.query_one(f"#{self.selected_id}").focus()

    def on_descendant_focus(self, event):
        self.selected_id = event.widget.id

    def _move(self, offset):
        buttons = [button for button in self.query(Button) if not button.disabled]
        if buttons:
            current = next((i for i, button in enumerate(buttons) if button.has_focus), 0)
            buttons[(current + offset) % len(buttons)].focus()

    def action_previous(self):
        self._move(-1)

    def action_next(self):
        self._move(1)


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

    class ActionRequested(Message):
        def __init__(self, action):
            super().__init__()
            self.action = action

    def compose(self) -> ComposeResult:
        with ConfigurationBar(id="composer-actions"):
            yield Button("模型", id="choose-model")
            yield Button("思考", id="choose-thinking")
            yield Button("计划", id="toggle-plan")
            yield Button("附件", id="choose-attachment")
            status = Static("", id="task-summary")
            status.display = False
            yield status
        yield Static("", id="composer-meta")
        with Horizontal(id="input-row"):
            yield Static("›", id="input-prompt")
            self._inp = Composer(id="chat-input", placeholder="描述任务，或按 Ctrl+P 选择操作")
            yield self._inp
        with Horizontal(id="composer-footer"):
            yield ActivityStatus(id="agent-activity")
            yield ContextFooter("", id="composer-hints")
            if self._hud is not None:
                yield self._hud

    def update_state(self, *, model, thinking, plan, attachments, draft, queued):
        self._queued = queued
        if draft and not self._inp.text:
            self._inp.value = draft
        meta = Text()
        if attachments:
            meta.append("附件 " + ", ".join(item["name"] for item in attachments))
        self.query_one("#composer-meta", Static).update(meta)
        self.query_one("#composer-meta").display = bool(attachments)
        for selector, label in [("choose-model", model), ("choose-thinking", f"思考 {thinking}"),
                                ("toggle-plan", "计划 开" if plan else "计划 关"), ("choose-attachment", f"附件 {len(attachments)}")]:
            self.query_one(f"#{selector}", Button).label = Text(label)
        self._update_hints()

    def toggle_multiline(self):
        self._inp.multiline = not self._inp.multiline
        self._update_size()
        self._update_hints()
        self._inp.focus()

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

    def on_button_pressed(self, event: Button.Pressed):
        event.stop()
        self.post_message(self.ActionRequested(event.button.id))
