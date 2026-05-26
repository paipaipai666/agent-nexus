"""Compaction helpers for short-term memory management."""

from __future__ import annotations

import re

RECOVERABLE_TOOLS = frozenset({
    "read",
    "bash",
    "grep",
    "glob",
    "web_search",
    "web_fetch",
    "edit",
    "write",
    "search",
})


def parse_tool_message(content: str) -> tuple[str | None, str | None]:
    """Parse a tool message to extract tool name and params."""
    match = re.match(r"Action:\s*([\w-]+)\[([^\]]*)\]", content)
    if match:
        return match.group(1), match.group(2)
    return None, None


def is_recoverable_tool(tool_name: str | None) -> bool:
    return bool(tool_name and tool_name.lower() in RECOVERABLE_TOOLS)
