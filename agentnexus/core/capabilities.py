"""Model capability detection — config overrides + live probe (no hardcoded catalog)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from agentnexus.core.config import get_settings

logger = logging.getLogger(__name__)


@dataclass
class ModelCapabilities:
    """What a model can and cannot do. Runtime overrides live in SessionCapabilityTracker."""

    # Core feature flags — conservative until probed or overridden
    supports_tool_calling: bool = False
    supports_json_mode: bool = False          # response_format={"type": "json_object"}
    supports_json_schema: bool = False        # structured output with schema
    supports_thinking: bool = False           # reasoning/thinking tokens
    supports_parallel_tool_calls: bool = False
    supports_system_role: bool = True
    supports_vision: bool = False            # image input (multimodal)

    # Token limits — 0 means unknown (do not invent vendor windows)
    max_context_tokens: int = 0
    max_output_tokens: int = 8_192

    # Thinking tuning
    thinking_budget_tokens: int = 4_000
    thinking_effort: str = "medium"            # "none"|"low"|"medium"|"high"

    # True when flags are still defaults (not probe/config/override) —
    # callers may probe the live endpoint instead of trusting these.
    from_default_fallback: bool = True


def detect_capabilities(model_id: str, base_url: str = "") -> ModelCapabilities:
    """Resolve capabilities from user config / model override, else unknown defaults.

    Priority: per-model config override > global config > conservative defaults.
    There is no baked-in vendor catalog — when flags are still defaults the
    caller may probe the endpoint (see AgentLLM.capabilities).
    Model ids are used exactly as configured (no vendor-prefix guessing).
    """
    caps = ModelCapabilities(from_default_fallback=True)

    # ── User config overrides ──
    settings = get_settings()
    if settings.model_tool_calling is not None:
        caps.supports_tool_calling = settings.model_tool_calling
        caps.from_default_fallback = False
    if settings.model_json_mode is not None:
        caps.supports_json_mode = settings.model_json_mode
        caps.from_default_fallback = False
    if settings.model_thinking is not None:
        caps.supports_thinking = settings.model_thinking
        caps.from_default_fallback = False

    # Per-model override (highest priority) — from the provider's model entry.
    entry = settings.find_model_override_entry(model_id, base_url)
    if entry is not None and entry.override is not None:
        ov = entry.override
        if ov.context_length is not None:
            caps.max_context_tokens = ov.context_length
        if ov.max_output_tokens is not None:
            caps.max_output_tokens = ov.max_output_tokens
        if ov.supports_vision is not None:
            caps.supports_vision = ov.supports_vision
        if ov.supports_tool_calling is not None:
            caps.supports_tool_calling = ov.supports_tool_calling
        if ov.supports_json_mode is not None:
            caps.supports_json_mode = ov.supports_json_mode
        if ov.supports_json_schema is not None:
            caps.supports_json_schema = ov.supports_json_schema
        if ov.supports_thinking is not None:
            caps.supports_thinking = ov.supports_thinking
        if ov.supports_parallel_tool_calls is not None:
            caps.supports_parallel_tool_calls = ov.supports_parallel_tool_calls
        if any(
            getattr(ov, name) is not None
            for name in (
                "context_length", "max_output_tokens", "supports_vision",
                "supports_tool_calling", "supports_json_mode", "supports_json_schema",
                "supports_thinking", "supports_parallel_tool_calls",
            )
        ):
            caps.from_default_fallback = False

    return caps


@dataclass
class SessionCapabilityTracker:
    """Per-session runtime capability override — tracks features disabled by API errors.

    Keeps a blocklist of features that failed at runtime so we don't keep retrying them.
    Isolated per-session — no cross-session pollution.
    """

    disabled_features: set[str] = field(default_factory=set)
    failed_counts: dict[str, int] = field(default_factory=dict)

    def mark_failed(self, feature: str, max_retries: int = 1) -> bool:
        """Record a failure. Returns True if feature is now disabled."""
        self.failed_counts[feature] = self.failed_counts.get(feature, 0) + 1
        if self.failed_counts[feature] >= max_retries:
            self.disabled_features.add(feature)
            return True
        return False

    def is_available(self, feature: str, base_support: bool) -> bool:
        """Check if feature is available given base capability + session history."""
        if feature in self.disabled_features:
            return False
        return base_support

    def reset(self, feature: str):
        """Clear failure tracking for a feature (e.g., after model switch)."""
        self.disabled_features.discard(feature)
        self.failed_counts.pop(feature, None)


def resolve_ctx_max(model_id: str, base_url: str = "") -> int | None:
    """Max context tokens from explicit model override only (never invented)."""
    caps = detect_capabilities(model_id, base_url)
    if caps.max_context_tokens > 0:
        return caps.max_context_tokens
    return None
