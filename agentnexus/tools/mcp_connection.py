"""Backward-compatible shim — imports from agentnexus.tools.mcp.connection."""

from agentnexus.tools.mcp.connection import *  # noqa: F401,F403
from agentnexus.tools.mcp.connection import build_http_client_kwargs, ensure_sdk_available

__all__ = ["build_http_client_kwargs", "ensure_sdk_available"]
