"""Backward-compatible shim — imports from agentnexus.tools.mcp.call."""

from agentnexus.tools.mcp.call import *  # noqa: F401,F403
from agentnexus.tools.mcp.call import call_descriptor, run_with_limiter

__all__ = ["call_descriptor", "run_with_limiter"]
