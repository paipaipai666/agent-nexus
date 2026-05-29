"""Base LLM provider interface and result container."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class StreamResult:
    """Unified result from a streaming LLM call."""

    text: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    reasoning_content: str = ""
    usage: dict[str, int] = field(default_factory=dict)
    finish_reason: str = ""

    @property
    def truncated(self) -> bool:
        return self.finish_reason in ("length", "max_tokens")


class BaseLLMProvider(ABC):
    """Abstract base for direct LLM API providers."""

    @abstractmethod
    def stream_chat(
        self,
        messages: list[dict[str, Any]],
        model: str,
        api_key: str,
        base_url: str,
        temperature: float = 0,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int | None = None,
        timeout: int = 60,
        parallel_tool_calls: bool | None = None,
        stream_options: dict[str, Any] | None = None,
        reasoning_effort: str | None = None,
    ) -> StreamResult:
        """Execute a streaming chat completion and return the unified result."""
