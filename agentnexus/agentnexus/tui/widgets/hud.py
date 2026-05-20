"""HUD — bottom status bar: model, context, tokens."""

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Static

from agentnexus.core.config import get_settings


def _resolve_ctx_max(model_id: str) -> int | None:
    """Query LiteLLM's model registry for the model's max input tokens."""
    try:
        from litellm import get_model_info
        info = get_model_info(model_id)
        return info.get("max_input_tokens") or info.get("max_output_tokens") or None
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
        self.input_tokens = 0
        self.output_tokens = 0

    def update_tokens(self, input_tokens: int, output_tokens: int):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self._refresh()

    def _refresh(self):
        try:
            self.query_one("#hud-text", Static).update(self._build_text())
        except Exception:
            pass

    def compose(self) -> ComposeResult:
        yield Static(self._build_text(), id="hud-text")

    def _build_text(self) -> str:
        ctx_used = self.input_tokens + self.output_tokens
        ctx_k = ctx_used / 1000

        if self.ctx_max is not None:
            ratio = ctx_used / self.ctx_max
            bar_len = 8
            filled = min(bar_len, int(ratio * bar_len))
            bar = f"[#fab283]{'█' * filled}[/][dim]{'░' * (bar_len - filled)}[/]"
            ctx_seg = f"ctx {ctx_k:.1f}k/{self.ctx_max / 1000:.0f}k {bar}"
        else:
            ctx_seg = f"ctx {ctx_k:.1f}k/[dim]128k[/]"

        parts = [
            f" [#6ba5f2]{self._display_model}[/]",
            f" [dim]│[/] {ctx_seg}",
            f" [dim]│[/] in:{self.input_tokens // 1000}k out:{self.output_tokens // 1000}k",
        ]
        return "".join(parts)
