"""Right side panel — version control, LTM, tool audit, CLI hints."""

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Label, Static


class SidePanel(Widget):
    """Auxiliary info panel with elevated card design."""

    DEFAULT_CSS = """
    SidePanel {
        height: 100%;
        overflow-y: auto;
    }
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._version_info = ("main", "—", False, False)
        self._ltm_items: list[str] = []
        self._tool_items: list[dict] = []

    def update_version(self, branch: str, head: str, can_undo: bool, can_redo: bool):
        self._version_info = (branch, head, can_undo, can_redo)
        self._refresh_card("version-card", self._render_version())

    def update_memory(self, items: list[str]):
        self._ltm_items = items
        self._refresh_card("memory-card", self._render_memory())

    def update_tools(self, items: list[dict]):
        self._tool_items = items
        self._refresh_card("tool-card", self._render_tools())

    def _refresh_card(self, card_id: str, content: str):
        try:
            card = self.query_one(f"#{card_id}", Static)
            card.update(content)
        except Exception:
            pass

    def _render_version(self) -> str:
        branch, head, can_undo, can_redo = self._version_info
        lines = [f"[bold]分支:[/] [#a78bfa]{branch}[/]"]
        lines.append(f"[dim]HEAD: {head}[/]")
        actions = []
        if can_undo:
            actions.append("[dim]/undo[/]")
        if can_redo:
            actions.append("[dim]/redo[/]")
        if actions:
            lines.append("  ".join(actions))
        return "\n".join(lines)

    def _render_memory(self) -> str:
        if not self._ltm_items:
            return "[dim]暂无记忆[/]"
        return "\n".join(f" [dim]·[/] {item}" for item in self._ltm_items[:5])

    def _render_tools(self) -> str:
        if not self._tool_items:
            return "[dim]暂无调用[/]"
        lines = []
        for t in self._tool_items[-5:]:
            status = "[#7fd88f]✓[/]" if t.get("ok", True) else "[#e06c75]✗[/]"
            lines.append(f" {status} {t['name']} [dim]{t.get('ms', 0):.0f}ms[/]")
        return "\n".join(lines)

    def compose(self) -> ComposeResult:
        with Static(classes="card"):
            yield Label("📋 版本控制", classes="card-title")
            yield Static(self._render_version(), id="version-card", classes="card-text")
        with Static(classes="card"):
            yield Label("🧠 长期记忆", classes="card-title")
            yield Static(self._render_memory(), id="memory-card", classes="card-text")
        with Static(classes="card"):
            yield Label("⚡ 最近工具", classes="card-title")
            yield Static(self._render_tools(), id="tool-card", classes="card-text")
        with Static(classes="card"):
            yield Label("💻 CLI 入口", classes="card-title")
            yield Static(
                "[dim]$[/] nexus eval run\n"
                "[dim]$[/] nexus stats\n"
                "[dim]$[/] nexus logs view",
                classes="card-dim"
            )
