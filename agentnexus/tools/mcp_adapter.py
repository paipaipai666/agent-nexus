"""Backward-compatible shim — imports from agentnexus.tools.mcp.adapter."""

from agentnexus.tools.mcp.adapter import *  # noqa: F401,F403
from agentnexus.tools.mcp.adapter import (
    MCPToolManager,
    _apply_capability_mcp_enabled,
    _content_block_to_text,
    _dump_sdk_object,
    _json_text,
    _normalize_prompt_result,
    _normalize_resource_result,
    _normalize_tool_result,
    _prompt_descriptor_from_sdk,
    _resource_descriptor_from_sdk,
    _sanitize_name,
    create_mcp_manager_from_settings,
)
from agentnexus.tools.mcp.schema import (
    MCPPromptDescriptor,
    MCPResourceDescriptor,
    MCPServerState,
    MCPToolDescriptor,
    ServerRuntime,
)

__all__ = [
    "MCPToolManager",
    "MCPServerState",
    "MCPToolDescriptor",
    "MCPResourceDescriptor",
    "MCPPromptDescriptor",
    "ServerRuntime",
    "create_mcp_manager_from_settings",
    "_sanitize_name",
    "_json_text",
    "_dump_sdk_object",
    "_normalize_tool_result",
    "_normalize_resource_result",
    "_normalize_prompt_result",
    "_content_block_to_text",
    "_resource_descriptor_from_sdk",
    "_prompt_descriptor_from_sdk",
    "_apply_capability_mcp_enabled",
]
