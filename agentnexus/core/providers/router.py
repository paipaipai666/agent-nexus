"""Provider router — selects the direct LLM provider for a model/endpoint.

Routing:
  - api.anthropic.com (or anthropic/* with no custom OpenAI-compatible base)
      → AnthropicMessagesProvider (/v1/messages)
  - everything else (OpenAI-compatible gateways, Azure OpenAI-compatible,
    DeepSeek, OpenRouter, vLLM, …) → OpenAIProvider (chat.completions)
"""

from __future__ import annotations

from agentnexus.core.providers.base import BaseLLMProvider

_openai_provider: BaseLLMProvider | None = None
_anthropic_provider: BaseLLMProvider | None = None


def _get_openai_provider() -> BaseLLMProvider:
    global _openai_provider
    if _openai_provider is None:
        from agentnexus.core.providers.openai_provider import OpenAIProvider

        _openai_provider = OpenAIProvider()
    return _openai_provider


def _get_anthropic_provider() -> BaseLLMProvider:
    global _anthropic_provider
    if _anthropic_provider is None:
        from agentnexus.core.providers.anthropic_provider import AnthropicMessagesProvider

        _anthropic_provider = AnthropicMessagesProvider()
    return _anthropic_provider


def _is_anthropic_wire(model: str, base_url: str) -> bool:
    url_lower = (base_url or "").lower()
    model_lower = (model or "").lower()
    if "anthropic.com" in url_lower:
        return True
    # Bare anthropic/* with no custom base → official Messages API.
    if model_lower.startswith("anthropic/") and not url_lower.strip():
        return True
    return False


def select_provider(model: str, base_url: str) -> BaseLLMProvider:
    """Return the direct provider for the given model/endpoint (never None)."""
    if _is_anthropic_wire(model, base_url):
        return _get_anthropic_provider()
    return _get_openai_provider()
