"""Compaction helpers for short-term memory management."""

from __future__ import annotations

import re
from typing import Any

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
    """Check if a tool is recoverable (static fallback list)."""
    return bool(tool_name and tool_name.lower() in RECOVERABLE_TOOLS)


class RecoverableToolChecker:
    """Dynamic recoverability checker backed by ToolRegistry.

    Falls back to the static RECOVERABLE_TOOLS frozenset when
    no registry is available or the tool is not registered.
    """

    def __init__(self, registry: Any | None = None):
        self._registry = registry

    def is_recoverable(self, tool_name: str | None) -> bool:
        if not tool_name:
            return False
        if self._registry:
            meta = self._registry.get_meta(tool_name)
            if meta is not None:
                return meta.recoverable
        return tool_name.lower() in RECOVERABLE_TOOLS

    def max_retention(self, tool_name: str) -> int:
        if self._registry:
            meta = self._registry.get_meta(tool_name)
            if meta is not None:
                return meta.max_retention
        return 5
