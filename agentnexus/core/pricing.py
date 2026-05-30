"""Shared LLM token pricing and cost calculation."""

# CNY per million tokens — (input_price, output_price)
_PRICING: dict[str, tuple[float, float]] = {
    "deepseek-v3": (1.0, 2.0),
    "deepseek-v4-flash": (0.6, 1.2),
    "deepseek-v4-pro": (1.0, 4.0),
    "deepseek-r1": (4.0, 16.0),
    "qwen-max": (2.5, 10.0),
    "gpt-4o": (17.5, 70.0),
    "gpt-4o-mini": (1.0, 4.0),
}

_MODEL_ALIASES: dict[str, str] = {
    "deepseek-chat": "deepseek-v3",
    "deepseek-reasoner": "deepseek-r1",
}


def resolve_model(model: str) -> str:
    """Resolve model aliases and find the pricing key."""
    model = _MODEL_ALIASES.get(model, model)
    for key in _PRICING:
        if key in model.lower():
            return key
    return model


def estimate_cost(input_tokens: int, output_tokens: int, model: str) -> float:
    """Estimate cost in CNY for given token counts and model."""
    key = resolve_model(model)
    prices = _PRICING.get(key)
    if not prices:
        return 0.0
    return (input_tokens * prices[0] + output_tokens * prices[1]) / 1_000_000
