"""Model capability detection and static registry."""

from __future__ import annotations

from dataclasses import dataclass, field
from fnmatch import fnmatch

from agentnexus.core.config import get_settings


@dataclass
class ModelCapabilities:
    """What a model can and cannot do. Immutable baseline — runtime overrides live in SessionCapabilityTracker."""

    # Core feature flags
    supports_tool_calling: bool = False
    supports_json_mode: bool = False          # response_format={"type": "json_object"}
    supports_json_schema: bool = False        # structured output with schema
    supports_thinking: bool = False           # reasoning/thinking tokens
    supports_parallel_tool_calls: bool = False
    supports_system_role: bool = True

    # Token limits
    max_context_tokens: int = 128_000
    max_output_tokens: int = 8_192

    # Thinking tuning
    thinking_budget_tokens: int = 4_000       # for Anthropic Claude 3.5/4.5
    thinking_effort: str = "medium"            # "none"|"low"|"medium"|"high"


# ── Static registry — prefix-matched, first-match-wins ──────────────────

CAPABILITY_REGISTRY: dict[str, ModelCapabilities] = {
    # DeepSeek V4 family
    "deepseek/deepseek-v4-pro":   ModelCapabilities(
        supports_tool_calling=True,
        supports_thinking=True,
        supports_parallel_tool_calls=True,
        max_context_tokens=262_144,
    ),
    "deepseek/deepseek-v4-flash": ModelCapabilities(
        supports_tool_calling=True,
        supports_thinking=True,
        supports_parallel_tool_calls=True,
        max_context_tokens=262_144,
    ),
    # Legacy DeepSeek
    "deepseek/deepseek-chat":     ModelCapabilities(
        supports_tool_calling=True,
        supports_json_mode=True,
        max_context_tokens=131_072,
    ),
    "deepseek/deepseek-reasoner": ModelCapabilities(
        supports_thinking=True,
        max_context_tokens=131_072,
    ),
    "deepseek/*":                 ModelCapabilities(
        supports_tool_calling=True,
        supports_json_mode=True,
    ),

    # OpenAI
    "openai/gpt-5*":   ModelCapabilities(
        supports_tool_calling=True,
        supports_json_mode=True,
        supports_json_schema=True,
        supports_thinking=True,
        supports_parallel_tool_calls=True,
    ),
    "openai/gpt-4*":   ModelCapabilities(
        supports_tool_calling=True,
        supports_json_mode=True,
        supports_json_schema=True,
        supports_parallel_tool_calls=True,
    ),
    "openai/o3*":      ModelCapabilities(
        supports_tool_calling=True,
        supports_json_mode=True,
        supports_json_schema=True,
        supports_thinking=True,
        supports_parallel_tool_calls=True,
    ),
    "openai/o4*":      ModelCapabilities(
        supports_tool_calling=True,
        supports_json_mode=True,
        supports_json_schema=True,
        supports_thinking=True,
        supports_parallel_tool_calls=True,
    ),
    "openai/*":        ModelCapabilities(
        supports_tool_calling=True,
        supports_json_mode=True,
        supports_json_schema=True,
    ),

    # Anthropic
    "anthropic/claude-4.6*": ModelCapabilities(
        supports_tool_calling=True,
        supports_thinking=True,
        supports_parallel_tool_calls=True,
        thinking_effort="medium",
        max_context_tokens=200_000,
    ),
    "anthropic/claude-4.5*": ModelCapabilities(
        supports_tool_calling=True,
        supports_thinking=True,
        supports_parallel_tool_calls=True,
        thinking_budget_tokens=4_096,
    ),
    "anthropic/claude-3.5*": ModelCapabilities(
        supports_tool_calling=True,
        supports_thinking=True,
        thinking_budget_tokens=4_096,
    ),
    "anthropic/*":           ModelCapabilities(
        supports_tool_calling=True,
    ),

    # Zhipu / GLM
    "zhipu/glm-4*": ModelCapabilities(supports_tool_calling=True),
    "zhipu/*":      ModelCapabilities(),

    # Ultimate fallback — most conservative
    "*": ModelCapabilities(),
}


def _normalize_model_id(model_id: str, base_url: str = "") -> str:
    """Add provider prefix if missing, based on base_url."""
    if "/" in model_id:
        return model_id
    base = (base_url or "").lower()
    if "deepseek.com" in base:
        return f"deepseek/{model_id}"
    elif "anthropic.com" in base:
        return f"anthropic/{model_id}"
    elif "zhipu.com" in base or "bigmodel.cn" in base:
        return f"zhipu/{model_id}"
    elif "openai.com" in base:
        return f"openai/{model_id}"
    else:
        # Default to openai for unknown providers
        return f"openai/{model_id}"


def _lookup_registry(model_id: str) -> ModelCapabilities:
    """Match model_id against the static registry. First match wins (ordered)."""
    for pattern, caps in CAPABILITY_REGISTRY.items():
        if fnmatch(model_id, pattern):
            return caps
    return ModelCapabilities()  # unreachable — "*" matches everything


def detect_capabilities(model_id: str, base_url: str = "") -> ModelCapabilities:
    """Merge static registry + litellm dynamic detection + config override.

    Priority: config override > litellm runtime > static registry > defaults.
    """
    normalized_id = _normalize_model_id(model_id, base_url)
    caps = _lookup_registry(normalized_id)

    # ── Dynamic detection via litellm ──
    try:
        import litellm
        if litellm.supports_function_calling(model=model_id):
            caps.supports_tool_calling = True
        if litellm.supports_response_schema(model=model_id):
            caps.supports_json_schema = True
        params = litellm.get_supported_openai_params(model=model_id)
        if params:
            if "response_format" in params:
                caps.supports_json_mode = True
            if "reasoning_effort" in params:
                caps.supports_thinking = True
            if "parallel_tool_calls" in params:
                caps.supports_parallel_tool_calls = True
        model_info = litellm.get_model_info(model=model_id)
        caps.max_context_tokens = model_info.get(
            "max_input_tokens", caps.max_context_tokens
        )
        caps.max_output_tokens = model_info.get(
            "max_output_tokens", caps.max_output_tokens
        )
    except Exception:
        pass  # litellm unavailable → stick with registry defaults

    # ── User config overrides ──
    settings = get_settings()
    if settings.model_tool_calling is not None:
        caps.supports_tool_calling = settings.model_tool_calling
    if settings.model_json_mode is not None:
        caps.supports_json_mode = settings.model_json_mode
    if settings.model_thinking is not None:
        caps.supports_thinking = settings.model_thinking

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


def model_candidates(model_id: str, base_url: str = "") -> list[str]:
    """Generate candidate model IDs for capability lookup."""
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


def registry_ctx_max(model_id: str, base_url: str = "") -> int | None:
    """Look up max context tokens from the static capability registry."""
    for candidate in model_candidates(model_id, base_url):
        for pattern, caps in CAPABILITY_REGISTRY.items():
            if pattern == "*":
                continue
            if fnmatch(candidate, pattern):
                return caps.max_context_tokens
    return None


def resolve_ctx_max_from_litellm(model_id: str) -> int | None:
    """Query LiteLLM for a model's max input tokens."""
    try:
        from litellm import get_model_info
        info = get_model_info(model_id)
        return info.get("max_input_tokens") or info.get("max_context_tokens") or None
    except Exception:
        return None


def resolve_ctx_max(model_id: str, base_url: str = "") -> int | None:
    """Resolve max context tokens from LiteLLM or static registry."""
    for candidate in model_candidates(model_id, base_url):
        value = resolve_ctx_max_from_litellm(candidate)
        if value:
            return value
    return registry_ctx_max(model_id, base_url)
