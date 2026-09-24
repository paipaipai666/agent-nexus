"""Provider router — picks the wire codec from the endpoint URL.

Routing:
  - base_url contains anthropic.com → AnthropicMessagesProvider (/v1/messages)
  - everything else → OpenAIProvider (chat.completions)

No model-id / vendor-prefix guessing — configure base_url to select the protocol.
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


def _is_anthropic_wire(base_url: str) -> bool:
    return "anthropic.com" in (base_url or "").lower()


def select_provider(model: str, base_url: str) -> BaseLLMProvider:
    """Return the direct provider for the given endpoint (never None)."""
    if _is_anthropic_wire(base_url):
        return _get_anthropic_provider()
    return _get_openai_provider()
