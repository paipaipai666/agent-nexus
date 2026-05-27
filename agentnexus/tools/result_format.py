"""Helpers for rendering structured tool results as compact text."""

from __future__ import annotations

from typing import Any


def summarize_tool_result(result: Any) -> str:
    """Convert a tool result into concise text for logs, runtime, and memory.

    Structured tools may return dicts with a human-readable ``message`` and
    optional preview content. This helper keeps runtime layers decoupled from
    tool-specific response schemas.
    """
    if not isinstance(result, dict):
        return str(result)

    message = result.get("message")
    if not isinstance(message, str) or not message.strip():
        return str(result)

    preview = result.get("preview")
    if not isinstance(preview, dict):
        return message

    preview_text = preview.get("text")
    if not isinstance(preview_text, str) or not preview_text.strip():
        return message

    return f"{message}\n\nDiff preview:\n{preview_text}"
