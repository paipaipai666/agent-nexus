"""Shared LLM token pricing and cost calculation."""

import logging

logger = logging.getLogger(__name__)

# CNY per million tokens — (input_price, output_price)
_PRICING: dict[str, tuple[float, float]] = {
    "deepseek-v3": (1.0, 2.0),
    "deepseek-v4-flash": (0.6, 1.2),
    "deepseek-v4-pro": (1.0, 4.0),
    "deepseek-r1": (4.0, 16.0),
    "qwen-max": (2.5, 10.0),
    "gpt-4o": (17.5, 70.0),
    "gpt-4o-mini": (1.0, 4.0),
    "claude-3.5-sonnet": (18.0, 90.0),
    "claude-4": (90.0, 450.0),
    "glm-4": (50.0, 50.0),
}

_MODEL_ALIASES: dict[str, str] = {
    "deepseek-chat": "deepseek-v3",
    "deepseek-reasoner": "deepseek-r1",
}

_DEFAULT_INPUT_PRICE = 10.0   # CNY per million tokens
_DEFAULT_OUTPUT_PRICE = 30.0  # CNY per million tokens


def resolve_model(model: str) -> str:
    """Resolve model aliases and find the pricing key."""
    model_lower = model.lower()
    model = _MODEL_ALIASES.get(model_lower, model)
    for key in _PRICING:
        if key in model_lower:
            return key
    return model


def estimate_cost(input_tokens: int, output_tokens: int, model: str) -> float:
    """Estimate cost in CNY for given token counts and model."""
    key = resolve_model(model)
    prices = _PRICING.get(key)
    if not prices:
        logger.warning("Unknown model '%s' — using default pricing estimate", model)
        prices = (_DEFAULT_INPUT_PRICE, _DEFAULT_OUTPUT_PRICE)
    return (input_tokens * prices[0] + output_tokens * prices[1]) / 1_000_000
