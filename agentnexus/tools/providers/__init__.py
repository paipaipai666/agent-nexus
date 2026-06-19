"""Tool provider abstractions and built-in provider registrations.

This subpackage splits the former monolithic ``providers`` module into
per-provider files while preserving the public import surface:

    from agentnexus.tools.providers import ToolProviderContext  # still works
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from agentnexus.tools.providers.base import (
    ProviderSpec,
    ToolProvider,
    ToolProviderContext,
)
from agentnexus.tools.providers.browser_provider import BrowserToolProvider
from agentnexus.tools.providers.codegraph_provider import CodeGraphToolProvider
from agentnexus.tools.providers.computer_use_provider import ComputerUseToolProvider
from agentnexus.tools.providers.execution_provider import ExecutionToolProvider
from agentnexus.tools.providers.filesystem_provider import FilesystemToolProvider
from agentnexus.tools.providers.mcp_bridge_provider import McpBridgeToolProvider
from agentnexus.tools.providers.memory_provider import MemoryToolProvider
from agentnexus.tools.providers.search_provider import SearchToolProvider
from agentnexus.tools.providers.subagent_provider import SubagentToolProvider
from agentnexus.tools.providers.todo_provider import TodoToolProvider

if TYPE_CHECKING:
    from agentnexus.tools.registry import ToolRegistry


def default_tool_providers() -> list[ToolProvider]:
    """Return the built-in provider order used by legacy registration."""

    return [
        MemoryToolProvider(),
        SearchToolProvider(),
        FilesystemToolProvider(),
        ExecutionToolProvider(),
        McpBridgeToolProvider(),
        SubagentToolProvider(),
        TodoToolProvider(),
        CodeGraphToolProvider(),
        BrowserToolProvider(),
        ComputerUseToolProvider(),
    ]


def register_tool_providers(
    executor: "ToolRegistry",
    providers: list[ToolProvider] | None = None,
    context: ToolProviderContext | None = None,
) -> list[str]:
    """Register all enabled providers and return names of tools added."""

    ctx = context or ToolProviderContext()
    for provider in providers or default_tool_providers():
        provider.register(executor, ctx.for_provider(provider.metadata().name))
    return ctx.registered_tools


__all__ = [
    "BrowserToolProvider",
    "CodeGraphToolProvider",
    "ComputerUseToolProvider",
    "ExecutionToolProvider",
    "FilesystemToolProvider",
    "McpBridgeToolProvider",
    "MemoryToolProvider",
    "ProviderSpec",
    "SearchToolProvider",
    "SubagentToolProvider",
    "TodoToolProvider",
    "ToolProvider",
    "ToolProviderContext",
    "default_tool_providers",
    "register_tool_providers",
]
