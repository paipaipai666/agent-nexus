"""Backward-compatible shim — imports from agentnexus.tools.mcp.result."""

from agentnexus.tools.mcp.result import *  # noqa: F401,F403
from agentnexus.tools.mcp.result import (
    content_block_to_text,
    dump_sdk_object,
    get_sdk_attr,
    json_text,
    normalize_prompt_result,
    normalize_resource_result,
    normalize_tool_result,
    sanitize_name,
)

__all__ = [
    "get_sdk_attr",
    "sanitize_name",
    "json_text",
    "dump_sdk_object",
    "normalize_tool_result",
    "normalize_resource_result",
    "normalize_prompt_result",
    "content_block_to_text",
]
