"""Backward-compatible shim — imports from agentnexus.tools.mcp.lifecycle."""

from agentnexus.tools.mcp.lifecycle import *  # noqa: F401,F403
from agentnexus.tools.mcp.lifecycle import (
    close_all,
    connect_all,
    connect_server,
    disconnect_server,
)

__all__ = [
    "connect_all",
    "connect_server",
    "disconnect_server",
    "close_all",
]
