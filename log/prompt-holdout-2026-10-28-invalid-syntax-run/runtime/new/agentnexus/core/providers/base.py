"""Base LLM provider interface and result container."""

from __future__ import annotations

import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any


def remaining_timeout(deadline: float) -> float:
    """Return the remaining absolute request budget or fail before more I/O."""
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("Model capability probe deadline exceeded")
    return remaining


@contextmanager
def request_deadline(deadline: float | None, close: Callable[[], None]):
    """Close a dedicated request transport when its absolute budget expires."""
    if deadline is None:
        yield
        return

    def cancel():
        try:
            close()
        except Exception:
            pass

    timer = threading.Timer(remaining_timeout(deadline), cancel)
    timer.name = "capability-deadline"
    timer.daemon = True
    timer.start()
    try:
        yield
    finally:
        timer.cancel()
        timer.join(timeout=0.1)


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

    def abort_active_stream(self, thread_id: int | None = None) -> None:
        """Abort the stream owned by a caller thread, if supported."""

    def close(self) -> None:
        """Release provider resources during application shutdown."""

    @abstractmethod
    def stream_chat(
        self,
        messages: list[dict[str, Any]],
        model: str,
        api_key: str,
        base_url: str,
        temperature: float = 0,
        tools: list[dict[str, Any]] | None = None,
        response_format: dict[str, Any] | None = None,
        max_tokens: int | None = None,
        timeout: int = 60,
        parallel_tool_calls: bool | None = None,
        stream_options: dict[str, Any] | None = None,
        reasoning_effort: str | None = None,
        on_token: Callable[..., None] | None = None,
        deadline: float | None = None,
    ) -> StreamResult:
        """Execute a streaming chat completion and return the unified result.

        ``on_token`` may be called as ``on_token(text)`` or
        ``on_token(text, is_reasoning=True)``.
        """
