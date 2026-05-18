"""HUD — bottom status bar: model, context, tokens, budget."""

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Static


class HUD(Widget):
    """Bottom status bar with segmented layout."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.model = "v4-flash"
        self.ctx_used = 0
        self.ctx_max = 8192
        self.tokens_in = 0
        self.tokens_out = 0
        self.budget_pct = 100.0

    def update_tokens(self, tokens_in: int, tokens_out: int):
        self.tokens_in += tokens_in
        self.tokens_out += tokens_out
        self.ctx_used = self.tokens_in + self.tokens_out
        self.budget_pct = max(0, 100 - (self.ctx_used / self.ctx_max) * 100)
        self.query_one("#hud-text", Static).update(self._build_text())

    def compose(self) -> ComposeResult:
        yield Static(self._build_text(), id="hud-text")

    def _build_text(self) -> str:
        # context bar
        bar_len = 8
        ratio = self.ctx_used / self.ctx_max if self.ctx_max else 0
        filled = min(bar_len, int(ratio * bar_len))
        bar = f"[#fab283]{'█' * filled}[/][dim]{'░' * (bar_len - filled)}[/]"

        # budget color
        if self.budget_pct > 50:
            bgt = f"[#7fd88f]{self.budget_pct:.0f}%[/]"
        elif self.budget_pct > 20:
            bgt = f"[#f5a742]{self.budget_pct:.0f}%[/]"
        else:
            bgt = f"[#e06c75]{self.budget_pct:.0f}%[/]"

        ctx_k = self.ctx_used / 1000
        ctx_max_k = self.ctx_max / 1000

        return (
            f" [#6ba5f2]{self.model}[/] "
            f"[dim]│[/] ctx {ctx_k:.1f}k/{ctx_max_k:.0f}k {bar}"
            f" [dim]│[/] in:{self.tokens_in // 1000}k out:{self.tokens_out // 1000}k"
            f" [dim]│[/] bgt {bgt}"
        )
