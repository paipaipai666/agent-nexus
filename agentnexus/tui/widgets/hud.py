"""HUD — bottom status bar: model, context, tokens."""

from fnmatch import fnmatch
from pathlib import Path

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Static

from agentnexus.core.config import get_settings


def _model_candidates(model_id: str, base_url: str = "") -> list[str]:
    candidates = [model_id]
    if "/" not in model_id:
        base = (base_url or "").lower()
        if "deepseek" in base:
            candidates.append(f"deepseek/{model_id}")
        elif "openai" in base:
            candidates.append(f"openai/{model_id}")
        elif "anthropic" in base or "claude" in model_id.lower():
            candidates.append(f"anthropic/{model_id}")
        elif "bigmodel" in base or model_id.lower().startswith("glm"):
            candidates.append(f"zhipu/{model_id}")
    return list(dict.fromkeys(candidates))


def _registry_ctx_max(model_id: str, base_url: str = "") -> int | None:
    try:
        from agentnexus.core.capabilities import CAPABILITY_REGISTRY
    except Exception:
        return None

    for candidate in _model_candidates(model_id, base_url):
        for pattern, caps in CAPABILITY_REGISTRY.items():
            if pattern == "*":
                continue
            if fnmatch(candidate, pattern):
                return caps.max_context_tokens
    return None


def _resolve_ctx_max(model_id: str, base_url: str = "") -> int | None:
    for candidate in _model_candidates(model_id, base_url):
        value = _resolve_ctx_max_from_litellm(candidate)
        if value:
            return value
    return _registry_ctx_max(model_id, base_url)


def _resolve_ctx_max_from_litellm(model_id: str) -> int | None:
    try:
        from litellm import get_model_info
        info = get_model_info(model_id)
        return info.get("max_input_tokens") or info.get("max_context_tokens") or None
    except Exception:
        return None


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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        settings = get_settings()
        full_id = settings.llm_model_id
        self.model = full_id
        self._display_model = full_id.split("/")[-1] if "/" in full_id else full_id
        self.ctx_max = _resolve_ctx_max(full_id, getattr(settings, "llm_base_url", ""))
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
        cwd = Path.cwd().resolve()
        self._cwd_display = cwd.name or str(cwd)

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

        if self.ctx_max is not None:
            ratio = ctx_used / max(self.ctx_max, 1)
            bar_len = 10
            filled = min(bar_len, int(ratio * bar_len))
            bar = f"[#fab283]{'█' * filled}[/][dim]{'░' * (bar_len - filled)}[/]"
            pct = min(999, int(ratio * 100))
            ctx_seg = f"ctx {_format_k(ctx_used)}/{_format_k(self.ctx_max)} {pct:02d}% {bar}"
        else:
            ctx_seg = f"ctx {_format_k(ctx_used)}/[dim]?[/]"

        # Capability indicators
        caps = ["[#a78bfa]\U0001f9e0[/]"] if self._supports_thinking else []
        if self._strategy:
            caps.append(self._strategy)
        caps_seg = f" [dim]({' '.join(caps)})[/]" if caps else ""

        # Compression indicator
        compact_indicator = " [#fab283]\u2699 compact[/]" if self._compacting else ""

        head_short = self._head[:8] if self._head and self._head != "---" else self._head
        version_actions = []
        if self._can_undo:
            version_actions.append("undo")
        if self._can_redo:
            version_actions.append("redo")
        actions_seg = f" {'/'.join(version_actions)}" if version_actions else ""
        version_seg = f"{self._branch}@{head_short}{actions_seg}"

        parts = [
            f"[#6ba5f2]{self._display_model}[/]{caps_seg}",
            f"[dim]\u2502[/] {ctx_seg}",
            f"[dim]\u2502[/] in:{_format_k(self.total_input)} out:{_format_k(self.total_output)}",
            f"[dim]\u2502[/] {version_seg}",
            f"[dim]\u2502[/] {self._cwd_display}",
            compact_indicator,
        ]
        return " ".join(parts)
