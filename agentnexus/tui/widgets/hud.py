"""HUD — bottom status bar: model, context, tokens."""

from pathlib import Path

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Static

from agentnexus.core.config import get_settings


def _resolve_ctx_max(model_id: str) -> int | None:
    try:
        from litellm import get_model_info
        info = get_model_info(model_id)
        return info.get("max_input_tokens") or None
    except Exception:
        return None


class HUD(Widget):
    """Bottom status bar — model, context window, token usage."""

    DEFAULT_CSS = """
    HUD {
        height: auto;
    }
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        settings = get_settings()
        full_id = settings.llm_model_id
        self.model = full_id
        self._display_model = full_id.split("/")[-1] if "/" in full_id else full_id
        self.ctx_max = _resolve_ctx_max(full_id)
        # Current context (STM) size — shown in the context bar
        self.current_tokens = 0
        # Cumulative usage
        self.total_input = 0
        self.total_output = 0
        # Compression indicator
        self._compacting = False
        # Capability indicators
        self._supports_thinking = False
        self._strategy = ""
        # Version / workspace indicators
        self._branch = "main"
        self._head = "---"
        self._can_undo = False
        self._can_redo = False
        self._cwd_display = str(Path.cwd().resolve())

    def update_capabilities(self, supports_thinking: bool, strategy: str = ""):
        self._supports_thinking = supports_thinking
        self._strategy = strategy
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

    def update_version(self, branch: str, head: str, can_undo: bool, can_redo: bool):
        self._branch = branch
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

    def _build_text(self) -> str:
        ctx_used = self.current_tokens
        ctx_k = ctx_used / 1000

        if self.ctx_max is not None:
            ratio = ctx_used / max(self.ctx_max, 1)
            bar_len = 8
            filled = min(bar_len, int(ratio * bar_len))
            bar = f"[#fab283]{'█' * filled}[/][dim]{'░' * (bar_len - filled)}[/]"
            ctx_seg = f"ctx {ctx_k:.1f}k/{self.ctx_max / 1000:.0f}k {bar}"
        else:
            ctx_seg = f"ctx {ctx_k:.1f}k/[dim]?[/]"

        # Capability indicators
        thinking_indicator = " [#a78bfa]\U0001f9e0[/]" if self._supports_thinking else ""
        strategy_str = f" [dim]\u2502[/] {self._strategy}" if self._strategy else ""

        # Compression indicator
        compact_indicator = " [#fab283]\u2699[/]" if self._compacting else ""

        head_short = self._head[:8] if self._head and self._head != "---" else self._head
        version_actions = []
        if self._can_undo:
            version_actions.append("undo")
        if self._can_redo:
            version_actions.append("redo")
        actions_seg = f" ({'/'.join(version_actions)})" if version_actions else ""
        version_seg = f"cwd:{self._cwd_display} [dim]\u2502[/] {self._branch}@{head_short}{actions_seg}"

        parts = [
            f" [#6ba5f2]{self._display_model}[/]",
            thinking_indicator,
            strategy_str,
            f" [dim]\u2502[/] {ctx_seg}",
            f" [dim]\u2502[/] in:{self.total_input // 1000}k out:{self.total_output // 1000}k",
            f" [dim]\u2502[/] {version_seg}",
            compact_indicator,
        ]
        return "".join(parts)
