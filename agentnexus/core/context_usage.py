"""Estimate the composition of a model request without retaining its contents."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

ContextCategory = Literal["system_prompt", "tools", "mcp", "skills", "memory", "conversation", "tool_results"]
CONTEXT_CATEGORIES: tuple[ContextCategory, ...] = (
    "system_prompt", "tools", "mcp", "skills", "memory", "conversation", "tool_results",
)


@dataclass(frozen=True)
class ContextFragment:
    """A named prompt fragment supplied by the prompt builder."""

    category: ContextCategory
    text: str
    source: tuple[int, str] | None = None


@dataclass(frozen=True)
class ContextUsageSnapshot:
    """Token estimates from one projected model request."""

    categories: tuple[tuple[ContextCategory, int], ...]
    measured_at: float
    has_images: bool

    def to_dict(self, *, capacity: int) -> dict:
        """Report estimates and remaining capacity for the current model window."""
        used = sum(tokens for _, tokens in self.categories)
        return {
            "categories": dict(self.categories),
            "used_tokens": used,
            "free_tokens": max(0, capacity - used),
            "estimated": True,
            "measured_at": self.measured_at,
            "has_images": self.has_images,
        }


@lru_cache(maxsize=1)
def _encoding():
    import tiktoken

    return tiktoken.get_encoding("cl100k_base")


def _tokens(value) -> int:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return len(_encoding().encode(text, disallowed_special=()))


def _system_tokens(index: int, content: str, fragments: tuple[ContextFragment, ...], counts: dict[ContextCategory, int]) -> None:
    remaining = _tokens(content)
    occupied: list[tuple[int, int]] = []
    for fragment in sorted(fragments, key=lambda item: len(item.text), reverse=True):
        if not fragment.text:
            continue
        if fragment.source is not None and fragment.source != (index, content):
            continue
        start = content.find(fragment.text)
        while start >= 0:
            end = start + len(fragment.text)
            if not any(start < right and end > left for left, right in occupied):
                break
            start = content.find(fragment.text, start + 1)
        if start < 0:
            continue
        end = start + len(fragment.text)
        occupied.append((start, end))
        count = min(remaining, _tokens(fragment.text))
        counts[fragment.category] += count
        remaining -= count
    counts["system_prompt"] += remaining


def estimate_context_usage(
    *, messages: list[dict], tools: list[dict] | None,
    fragments: tuple[ContextFragment, ...], mcp_tool_names: frozenset[str],
) -> ContextUsageSnapshot:
    """Count text, message framing, tool calls, and native schemas by source.

    Estimates use cl100k_base and four framing tokens per message. Image token
    costs depend on the provider and are excluded rather than encoding base64.
    """
    counts: dict[ContextCategory, int] = dict.fromkeys(CONTEXT_CATEGORIES, 0)
    counts["system_prompt"] = 3
    has_images = False
    for index, message in enumerate(messages):
        role = message.get("role", "user")
        category: ContextCategory = (
            "system_prompt" if role == "system" else "tool_results" if role == "tool" else "conversation"
        )
        counts[category] += 4
        content = message.get("content") or ""
        if isinstance(content, list):
            text_parts = [part.get("text", "") for part in content if part.get("type") == "text"]
            has_images |= any(part.get("type") in {"image_url", "input_image", "image"} for part in content)
            content = "\n".join(text_parts)
        if role == "system":
            _system_tokens(index, content, fragments, counts)
        else:
            counts[category] += _tokens(content)
        for key in ("name", "tool_call_id", "tool_calls", "reasoning_content"):
            if message.get(key):
                counts[category] += _tokens(message[key])
    for tool in tools or []:
        category = "mcp" if tool.get("function", {}).get("name") in mcp_tool_names else "tools"
        counts[category] += _tokens(tool)
    return ContextUsageSnapshot(tuple(counts.items()), time.time(), has_images)
