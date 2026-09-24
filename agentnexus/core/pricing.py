"""Shared LLM token pricing and cost calculation.

No baked-in vendor price table — rates go stale and silently skew cost
reports. Use per-model override (input/output CNY per million tokens) or the
conservative default estimate below.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_DEFAULT_INPUT_PRICE = 10.0   # CNY per million tokens
_DEFAULT_OUTPUT_PRICE = 30.0  # CNY per million tokens


def resolve_model(model: str) -> str:
    """Normalize a model id for pricing lookups (identity without a vendor table)."""
    return (model or "").strip()


def _override_prices(model: str) -> tuple[float, float] | None:
    try:
        from agentnexus.core.config import get_settings

        settings = get_settings()
        target = (model or "").strip()
        for p in settings.llm_providers:
            for m in p.models:
                if m.model_id != target or m.override is None:
                    continue
                ov = m.override
                inp = getattr(ov, "input_price_cny_per_mtok", None)
                out = getattr(ov, "output_price_cny_per_mtok", None)
                if inp is not None or out is not None:
                    return (
                        float(inp) if inp is not None else _DEFAULT_INPUT_PRICE,
                        float(out) if out is not None else _DEFAULT_OUTPUT_PRICE,
                    )
    except Exception:
        return None
    return None


def estimate_cost(input_tokens: int, output_tokens: int, model: str) -> float:
    """Estimate cost in CNY for given token counts and model."""
    prices = _override_prices(model)
    if prices is None:
        prices = (_DEFAULT_INPUT_PRICE, _DEFAULT_OUTPUT_PRICE)
    return (input_tokens * prices[0] + output_tokens * prices[1]) / 1_000_000
