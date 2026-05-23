"""ConfirmDialog — ModalScreen for HITL confirmation of high-risk tool calls."""

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static


class ConfirmDialog(ModalScreen[bool]):
    """Modal confirmation dialog for dangerous tool operations.

    Returns True if user confirms, False if cancelled.
    """

    DEFAULT_CSS = """
    ConfirmDialog {
        align: center middle;
    }

    ConfirmDialog > Vertical {
        background: $surface;
        border: thick $warning;
        padding: 1 2;
        width: 60;
        max-height: 80%;
    }

    ConfirmDialog Label {
        width: 100%;
    }

    ConfirmDialog #confirm-title {
        text-style: bold;
        color: $warning;
        padding-bottom: 1;
    }

    ConfirmDialog #confirm-summary {
        color: $text;
        padding-top: 1;
        padding-bottom: 1;
    }

    ConfirmDialog #confirm-preview {
        background: $panel-darken-1;
        color: $text;
        padding: 1;
        margin-top: 1;
        margin-bottom: 1;
        height: auto;
        max-height: 12;
        overflow-y: auto;
    }

    ConfirmDialog Horizontal {
        width: 100%;
        align-horizontal: center;
        padding-top: 1;
    }

    ConfirmDialog Button {
        margin: 0 2;
    }
    """

    def __init__(self, tool_name: str, params_summary: str, risk_level: str = "high"):
        super().__init__()
        self._tool_name = tool_name
        self._params_summary = params_summary[:500]
        self._risk_level = risk_level

    def compose(self) -> ComposeResult:
        risk_label = {"high": "HIGH 高", "medium": "MEDIUM 中", "low": "LOW 低"}
        risk_color = {"high": "#e06c75", "medium": "#e5c07b", "low": "#98c379"}

        with Vertical():
            yield Label(
                "[!] 危险操作确认",
                id="confirm-title",
            )
            yield Label(
                f"工具: {self._tool_name}    风险: [{risk_color.get(self._risk_level, '')}]"
                f"{risk_label.get(self._risk_level, self._risk_level)}[/]",
            )
            yield Static(
                self._params_summary,
                id="confirm-preview",
            )
            yield Label(
                "是否确认执行此操作？",
                id="confirm-summary",
            )
            with Horizontal():
                yield Button("[y] 确认执行", variant="warning", id="btn-confirm")
                yield Button("[n] 取消", variant="default", id="btn-cancel")

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "btn-confirm":
            self.dismiss(True)
        else:
            self.dismiss(False)

    def on_key(self, event):
        if event.key in ("y", "Y"):
            self.dismiss(True)
        elif event.key in ("n", "N", "escape"):
            self.dismiss(False)
