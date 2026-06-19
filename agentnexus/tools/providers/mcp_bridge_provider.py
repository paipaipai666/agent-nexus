"""MCP bridge tool provider — bridge tools from configured MCP servers."""

from __future__ import annotations

from agentnexus.tools.providers.base import ProviderSpec, ToolProviderContext
from agentnexus.tools.registry import ToolRegistry


class McpBridgeToolProvider:
    def metadata(self) -> ProviderSpec:
        return ProviderSpec("mcp-bridge", description="Bridge tools discovered from configured MCP servers.")

    def register(self, executor: ToolRegistry, context: ToolProviderContext) -> None:
        if context.mcp_manager is None:
            return
        before = set(executor.list_tools())
        context.mcp_manager.register_tools(executor, include_tools=context.include_tools)
        context.mark_registered(executor, before)
