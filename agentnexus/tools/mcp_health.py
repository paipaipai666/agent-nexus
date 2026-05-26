"""Health and reconnect policy helpers for MCP runtimes."""

from __future__ import annotations

import time

from agentnexus.core.config import MCPServerConfig
from agentnexus.tools.mcp_schema import MCPServerState, ServerRuntime


def schedule_reconnect(server: MCPServerConfig, runtime: ServerRuntime | None) -> None:
    if runtime is None:
        return
    attempts = runtime.reconnect_attempts
    if server.reconnect_max_attempts and attempts >= server.reconnect_max_attempts:
        return
    delay = min(server.reconnect_max_delay_sec, server.reconnect_initial_delay_sec * (2 ** attempts))
    runtime.reconnect_attempts += 1
    runtime.next_reconnect_at = time.time() + delay


def should_attempt_reconnect(
    server: MCPServerConfig,
    runtime: ServerRuntime | None,
    now: float,
    *,
    closing: bool,
    has_failure: bool,
) -> bool:
    if closing:
        return False
    if runtime is None:
        return has_failure
    next_reconnect_at = getattr(runtime, "next_reconnect_at", None)
    if next_reconnect_at is None:
        return False
    return now >= next_reconnect_at


def mark_runtime_failure(runtime: ServerRuntime, exc: Exception) -> tuple[str | None, str]:
    failure = str(exc)
    server_name = getattr(getattr(runtime, "config", None), "name", None)
    runtime.consecutive_failures = getattr(runtime, "consecutive_failures", 0) + 1
    runtime.last_failure = failure
    runtime.state = MCPServerState.DEGRADED
    return server_name, failure


def mark_runtime_healthy(runtime: ServerRuntime) -> None:
    runtime.last_ping_at = time.time()
    runtime.consecutive_failures = 0
    runtime.last_failure = None
    runtime.state = MCPServerState.HEALTHY
