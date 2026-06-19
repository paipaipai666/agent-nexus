"""Backward-compatible shim — imports from agentnexus.tools.mcp.capabilities."""

from agentnexus.tools.mcp.capabilities import *  # noqa: F401,F403
from agentnexus.tools.mcp.capabilities import (
    _internal_descriptor,
    build_prompt_tool_descriptors,
    build_resource_tool_descriptors,
    import_prompts,
    import_resources,
    import_server_capabilities,
)

__all__ = [
    "import_server_capabilities",
    "import_resources",
    "import_prompts",
    "build_resource_tool_descriptors",
    "build_prompt_tool_descriptors",
    "_internal_descriptor",
]
