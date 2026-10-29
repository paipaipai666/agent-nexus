"""Provider router — picks the wire codec from the endpoint URL.

Routing:
  - base_url contains anthropic.com → AnthropicMessagesProvider (/v1/messages)
  - everything else → OpenAIProvider (chat.completions)

No model-id / vendor-prefix guessing — configure base_url to select the protocol.
"""

from __future__ import annotations

import atexit
import threading

from agentnexus.core.providers.base import BaseLLMProvider

_openai_provider: BaseLLMProvider | None = None
_anthropic_provider: BaseLLMProvider | None = None
_provider_lock = threading.RLock()


def _get_openai_provider() -> BaseLLMProvider:
    global _openai_provider
    with _provider_lock:
        if _openai_provider is None:
            from agentnexus.core.providers.openai_provider import OpenAIProvider

            _openai_provider = OpenAIProvider()
        return _openai_provider


def _get_anthropic_provider() -> BaseLLMProvider:
    global _anthropic_provider
    with _provider_lock:
        if _anthropic_provider is None:
            from agentnexus.core.providers.anthropic_provider import AnthropicMessagesProvider

            _anthropic_provider = AnthropicMessagesProvider()
        return _anthropic_provider


def close_providers() -> None:
    """Release the process-wide provider pools at application shutdown."""
    global _openai_provider, _anthropic_provider
    with _provider_lock:
        providers = [_openai_provider, _anthropic_provider]
        _openai_provider = _anthropic_provider = None
    for provider in providers:
        if provider is not None:
            provider.close()


atexit.register(close_providers)


def _is_anthropic_wire(base_url: str) -> bool:
    return "anthropic.com" in (base_url or "").lower()


def select_provider(model: str, base_url: str) -> BaseLLMProvider:
    """Return the direct provider for the given endpoint (never None)."""
    if _is_anthropic_wire(base_url):
        return _get_anthropic_provider()
    return _get_openai_provider()
