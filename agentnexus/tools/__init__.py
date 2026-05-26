"""AgentNexus tools — shared registration and public API."""

from __future__ import annotations

from agentnexus.tools.providers import (
    ProviderSpec,
    ToolProvider,
    ToolProviderContext,
    default_tool_providers,
    register_tool_providers,
)
from agentnexus.tools.tool_executor import ToolExecutor


def register_all_tools(
    executor: ToolExecutor,
    non_interactive: bool = False,
    llm_client=None,
    include_tools: set[str] | None = None,
    enable_subagent: bool = True,
    subagent_confirm=None,
    mcp_manager=None,
    extra_providers: list[ToolProvider] | None = None,
):
    """Register all available tools on the given executor.

    This remains the compatibility entry point for existing CLI/TUI/tests.
    Internally, tool discovery is now provider-based so new tool groups can be
    added by contributing a ToolProvider instead of editing this function.
    """

    context = ToolProviderContext(
        non_interactive=non_interactive,
        llm_client=llm_client,
        include_tools=include_tools,
        enable_subagent=enable_subagent,
        subagent_confirm=subagent_confirm,
        mcp_manager=mcp_manager,
    )
    providers = [*default_tool_providers(), *(extra_providers or [])]
    return register_tool_providers(executor, providers, context)


__all__ = [
    "ProviderSpec",
    "ToolProvider",
    "ToolProviderContext",
    "ToolExecutor",
    "default_tool_providers",
    "register_all_tools",
    "register_tool_providers",
]
