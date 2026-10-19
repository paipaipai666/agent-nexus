"""HUD — bottom status bar: model, context, tokens."""

from pathlib import Path

from rich.markup import escape
from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Static

from agentnexus.core.capabilities import resolve_ctx_max
from agentnexus.core.config import get_settings


def _format_k(tokens: int | float) -> str:
    value = float(tokens)
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}m"
    if value >= 10_000:
        return f"{value / 1000:.0f}k"
    if value >= 1000:
        return f"{value / 1000:.1f}k"
    return str(int(value))


class HUD(Widget):
    """Bottom status bar — model, context window, token usage."""
    DEFAULT_CSS = """
    HUD {
        height: auto;
    }
    """

    def __init__(self, *args, compact=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.compact = compact
        if compact:
            self.tooltip = "F1 查看用量与按键"
        settings = get_settings()
        full_id, base_url, _key, _timeout = settings.get_active_llm_profile()
        self.model = full_id
        self._display_model = full_id.split("/")[-1] if "/" in full_id else full_id
        self.ctx_max = resolve_ctx_max(full_id, base_url)
        self.current_tokens = 0
        self.total_input = 0
        self.total_output = 0
        self._compacting = False
        self._supports_thinking = False
        self._strategy = ""
        self._head = "---"
        self._can_undo = False
        self._can_redo = False
        cwd = Path.cwd().resolve()
        self._cwd_display = cwd.name or str(cwd)

    def update_capabilities(self, supports_thinking: bool, strategy: str = ""):
        self._supports_thinking = supports_thinking
        self._strategy = strategy
        self._refresh()

    def update_model(self, model: str, base_url: str = "", *, workspace: str | None = None):
        """Refresh model and workspace values for the active session."""
        self.model = model
        self._display_model = model.rsplit("/", 1)[-1]
        self.ctx_max = resolve_ctx_max(model, base_url)
        if workspace:
            path = Path(workspace)
            self._cwd_display = path.name or str(path)
        self._refresh()

    def update_context(self, current_tokens: int, total_input: int = 0, total_output: int = 0):
        """Update HUD with current context size and cumulative totals."""
        self.current_tokens = current_tokens
        self.total_input = total_input
        self.total_output = total_output
        self._refresh()

    def update_tokens(self, input_tokens: int, output_tokens: int):
        """Backward compatible: updates cumulative counters only."""
        self.total_input = input_tokens
        self.total_output = output_tokens
        self._refresh()

    def set_compacting(self, active: bool):
        self._compacting = active
        self._refresh()

    def update_version(self, head: str, can_undo: bool, can_redo: bool):
        self._head = head
        self._can_undo = can_undo
        self._can_redo = can_redo
        self._refresh()

    def _refresh(self):
        try:
            self.query_one("#hud-text", Static).update(self._build_text())
        except Exception:
            pass

    def compose(self) -> ComposeResult:
        yield Static(self._build_text(), id="hud-text")

    async def on_click(self):
        if self.compact:
            await self.screen.action_perform("help")

    def describe_usage(self):
        """Expose full session metrics without expanding the terminal status row."""
        return (f"模型 {self.model}\n上下文 {self.current_tokens} / {self.ctx_max if self.ctx_max is not None else '未报告上限'}\n"
                f"累计输入 {self.total_input}\n累计输出 {self.total_output}\n"
                f"思考能力 {'支持' if self._supports_thinking else '不支持'}\n工具策略 {self._strategy or '未报告'}\n检查点 {self._head}")

    def _build_text(self) -> str:
        ctx_used = self.current_tokens

        if self.ctx_max is not None:
            ratio = ctx_used / max(self.ctx_max, 1)
            pct = min(999, int(ratio * 100))
            ctx_seg = f"ctx {_format_k(ctx_used)}/{_format_k(self.ctx_max)} {pct:02d}%"
        else:
            ctx_seg = f"ctx {_format_k(ctx_used)}/[dim]?[/]"

        caps = ["思考"] if self._supports_thinking else []
        if self._strategy:
            caps.append(self._strategy)
        caps_seg = f" [dim]({' '.join(caps)})[/]" if caps else ""

        compact_indicator = " · compact 压缩中" if self._compacting else ""

        if self.compact:
            return "压缩中 · " + ctx_seg if self._compacting else ctx_seg

        head_short = self._head[:8] if self._head and self._head != "---" else self._head
        version_actions = []
        if self._can_undo:
            version_actions.append("undo")
        if self._can_redo:
            version_actions.append("redo")
        actions_seg = f" {'/'.join(version_actions)}" if version_actions else ""
        version_seg = f"{head_short}{actions_seg}"

        parts = [
            f"{escape(self._display_model)}{caps_seg}",
            f"[dim]\u2502[/] {ctx_seg}",
            f"[dim]\u2502[/] in:{_format_k(self.total_input)} out:{_format_k(self.total_output)}",
            f"[dim]\u2502[/] {version_seg}",
            compact_indicator,
        ]
        return " ".join(parts)
