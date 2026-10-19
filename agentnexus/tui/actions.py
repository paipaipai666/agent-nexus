"""Shared action definitions for terminal keys, hints and pointer activation."""

from dataclasses import dataclass
from typing import Literal

from rich.cells import cell_len
from rich.style import Style
from rich.text import Text
from textual.binding import Binding
from textual.widgets import Static


@dataclass(frozen=True)
class TerminalAction:
    """One user operation with a stable identity and a single execution target."""

    id: str
    title: str
    key: str
    handler: str
    description: str = ""
    scope: Literal["screen", "records", "controls", "composer"] = "screen"
    key_display: str = ""

    @property
    def display_key(self):
        return self.key_display or self.key.replace("ctrl", "Ctrl").replace("alt", "Alt").title()

    def binding(self):
        return Binding(self.key, f"perform('{self.id}')", self.title, priority=self.scope == "screen")


@dataclass(frozen=True)
class ActionState:
    """An operation and the reason it is unavailable in the current context."""

    action: TerminalAction
    disabled_reason: str = ""


def describe_actions(states):
    """Describe the currently available keys and explain unavailable operations."""
    lines = []
    for state in states:
        action = state.action
        detail = state.disabled_reason or action.description
        lines.append(f"{action.display_key}  {action.title}" + (f"\n  {detail}" if detail else ""))
    return "\n\n".join(lines)


SCREEN_ACTIONS = (
    TerminalAction("menu", "操作", "ctrl+p", "command_menu"),
    TerminalAction("sessions", "会话", "f2", "session_menu"),
    TerminalAction("tasks", "任务", "f3", "toggle_inspector"),
    TerminalAction("multiline", "多行", "f4", "edit_message", "切换 Enter 的发送与换行行为"),
    TerminalAction("models", "模型", "alt+m", "choose_model"),
    TerminalAction("thinking", "思考强度", "alt+t", "choose_thinking"),
    TerminalAction("attachments", "附件", "alt+a", "choose_attachment"),
    TerminalAction("plan", "计划模式", "alt+p", "toggle_plan"),
    TerminalAction("help", "帮助", "f1", "context_help"),
    TerminalAction("next-region", "浏览", "tab", "next_region", "在输入、记录和配置间切换；有命令候选时 Tab 补全"),
    TerminalAction("previous-region", "上一区域", "shift+tab", "previous_region"),
    TerminalAction("previous-page", "上翻对话", "pageup", "previous_page", key_display="PageUp"),
    TerminalAction("next-page", "下翻对话", "pagedown", "next_page", key_display="PageDown"),
    TerminalAction("latest", "最新输出", "ctrl+end", "latest_output"),
    TerminalAction("stop", "复制 / 停止", "ctrl+c", "stop_task", "有选区时复制；任务运行且没有选区时停止"),
    TerminalAction("input", "返回输入", "escape", "focus_input", key_display="Esc"),
)

RECORD_ACTIONS = (
    TerminalAction("previous-record", "上一条", "up", "previous_record", scope="records", key_display="↑"),
    TerminalAction("next-record", "下一条", "down", "next_record", scope="records", key_display="↓"),
    TerminalAction("open-record", "展开 / 查看", "enter,space", "open_record", scope="records", key_display="Enter"),
    TerminalAction("full-record", "全文", "d", "full_record", scope="records", key_display="D"),
)

CONTROL_ACTIONS = (
    TerminalAction("previous-control", "上一项", "left", "previous", scope="controls", key_display="←"),
    TerminalAction("next-control", "下一项", "right", "next", scope="controls", key_display="→"),
)

COMPOSER_ACTIONS = (
    TerminalAction("send", "发送", "ctrl+s", "submit", "Ctrl+S 始终发送当前消息", scope="composer"),
    TerminalAction("newline", "换行", "enter", "newline", "Shift+Enter、Alt+Enter 或 Ctrl+J 也可换行", scope="composer"),
    TerminalAction("accept-command", "确认", "enter", "accept_command"),
    TerminalAction("complete-command", "补全", "tab", "complete_command"),
)

ACTIONS = {action.id: action for action in (*SCREEN_ACTIONS, *RECORD_ACTIONS, *CONTROL_ACTIONS, *COMPOSER_ACTIONS)}
CONTROL_IDS = {"choose-model": "models", "choose-thinking": "thinking", "toggle-plan": "plan", "choose-attachment": "attachments"}


class ContextFooter(Static):
    """Clickable hints generated from the same operations as the keyboard bindings."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._states = None
        self._prefix = ""

    def show_actions(self, states, *, prefix=""):
        self._states, self._prefix = list(states), prefix
        self._render_actions()

    def on_resize(self):
        if self._states is not None:
            self._render_actions()

    def _render_actions(self):
        states, prefix = list(self._states), self._prefix
        pinned = {"help", "cancel", "close", "input", "save"}
        if states:
            pinned.add(states[0].action.id)
        width = self.size.width
        while width and cell_len(" · ".join(filter(None, [prefix, *(f"{state.action.display_key} {state.action.title}" for state in states)]))) > width:
            removable = next((i for i in range(len(states) - 1, -1, -1) if states[i].action.id not in pinned), None)
            if removable is not None:
                states.pop(removable)
            elif prefix:
                prefix = ""
            else:
                break
        text = Text(prefix)
        for state in states:
            if text:
                text.append(" · ")
            start = len(text)
            text.append(f"{state.action.display_key} {state.action.title}")
            if state.disabled_reason:
                text.stylize("dim", start)
            else:
                text.stylize(Style(meta={"@click": f"screen.perform('{state.action.id}')"}), start)
        self.update(text)
