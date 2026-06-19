"""Backward-compatible shim — imports from agentnexus.tools.mcp.descriptors."""

from agentnexus.tools.mcp.descriptors import *  # noqa: F401,F403
from agentnexus.tools.mcp.descriptors import (
    build_local_tool_name,
    build_tool_descriptor,
    normalize_param_schema,
    prompt_descriptor_from_sdk,
    resource_descriptor_from_sdk,
    should_import_tool,
)

__all__ = [
    "build_local_tool_name",
    "normalize_param_schema",
    "should_import_tool",
    "build_tool_descriptor",
    "resource_descriptor_from_sdk",
    "prompt_descriptor_from_sdk",
]
