"""Helpers for rendering structured tool results as compact text."""

from __future__ import annotations

import json
import re
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


def extract_diff_parts(result: Any) -> tuple[str, str] | None:
    """Extract message and diff text from a structured tool result.

    Returns (message, diff_text) if result is a dict with preview,
    or None if not a structured result.
    """
    if not isinstance(result, dict):
        return None

    message = result.get("message")
    if not isinstance(message, str) or not message.strip():
        return None

    preview = result.get("preview")
    if not isinstance(preview, dict):
        return (message, "")

    preview_text = preview.get("text")
    if not isinstance(preview_text, str) or not preview_text.strip():
        return (message, "")

    return (message, preview_text)


def condense_search_result(text: str) -> str:
    """Show only title/score/URL from web_search; skip full content body.

    Input format (from web_search.py):
      [N] Title (date) [相关度: X.XX]
      URL: https://...
      <multi-line content body>
    """
    lines = text.split("\n")
    out = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if re.match(r"^\[\d+\]", stripped) or stripped.startswith("URL:"):
            out.append(line)
    return "\n".join(out) if out else text[:500]


def condense_file_result(text: str) -> str:
    """Show only file metadata line from file_read; skip full file content.

    Input format (from file_ops.py):
      [文件] path (N 行, 共 X 字节)
      1 | line content...
      2 | line content...
    """
    first_line = text.split("\n")[0] if text else ""
    if first_line.startswith("[文件]"):
        return first_line
    return text[:200]


def format_subagent_result(text: str) -> str:
    """Format subagent_run JSON output into a readable delegation summary."""
    try:
        payload = json.loads(text)
    except Exception:
        return text[:500]

    if not isinstance(payload, dict):
        return text[:500]

    role = payload.get("role", "general")
    status = payload.get("status", "unknown")
    steps_used = payload.get("steps_used", 0)
    allowed_tools = payload.get("allowed_tools", []) or []
    answer = str(payload.get("answer", "") or "").strip()
    summary = str(payload.get("summary", "") or "").strip()

    lines = [
        f"[子代理] role={role} status={status} steps={steps_used}",
        f"tools: {', '.join(allowed_tools) if allowed_tools else '-'}",
    ]
    if answer:
        lines.append(f"answer: {answer[:400]}")
    elif summary:
        lines.append(f"summary: {summary[:400]}")

    return "\n".join(lines)
