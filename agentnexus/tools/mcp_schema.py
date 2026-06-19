"""Backward-compatible shim — imports from agentnexus.tools.mcp.schema."""

from agentnexus.tools.mcp.schema import *  # noqa: F401,F403
from agentnexus.tools.mcp.schema import (
    MCPPromptDescriptor,
    MCPResourceDescriptor,
    MCPServerState,
    MCPToolDescriptor,
    ServerRuntime,
)

__all__ = [
    "MCPServerState",
    "MCPToolDescriptor",
    "MCPResourceDescriptor",
    "MCPPromptDescriptor",
    "ServerRuntime",
]
