"""Provider router — selects the appropriate direct LLM provider.

Routing rules:
  - anthropic/*  → skip direct (use LiteLLM fallback)
  - Azure URLs   → skip direct (use LiteLLM fallback)
  - everything else → OpenAIProvider
"""

from __future__ import annotations

from agentnexus.core.providers.base import BaseLLMProvider

_openai_provider: BaseLLMProvider | None = None


def _get_openai_provider() -> BaseLLMProvider:
    global _openai_provider
    if _openai_provider is None:
        from agentnexus.core.providers.openai_provider import OpenAIProvider
        _openai_provider = OpenAIProvider()
    return _openai_provider


def select_provider(model: str, base_url: str) -> BaseLLMProvider | None:
    """Return a direct provider for the given model, or None to use LiteLLM fallback."""
    model_lower = (model or "").lower()
    url_lower = (base_url or "").lower()

    # Anthropic models — message format differs, use LiteLLM
    if model_lower.startswith("anthropic/"):
        return None

    # Azure OpenAI — API path/auth differs, use LiteLLM
    if "azure" in url_lower:
        return None

    # Everything else — OpenAI-compatible
    return _get_openai_provider()


def get_provider(model: str, base_url: str) -> BaseLLMProvider | None:
    """Alias for select_provider."""
    return select_provider(model, base_url)
