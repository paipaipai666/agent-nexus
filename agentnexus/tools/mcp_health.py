"""Backward-compatible shim — imports from agentnexus.tools.mcp.health."""

from agentnexus.tools.mcp.health import *  # noqa: F401,F403
from agentnexus.tools.mcp.health import (
    mark_runtime_failure,
    mark_runtime_healthy,
    schedule_reconnect,
    should_attempt_reconnect,
)

__all__ = [
    "schedule_reconnect",
    "should_attempt_reconnect",
    "mark_runtime_failure",
    "mark_runtime_healthy",
]
