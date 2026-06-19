"""MCP adapter state and descriptor models."""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from agentnexus.core.config import MCPServerConfig


class MCPServerState(StrEnum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    RECONNECTING = "reconnecting"
    CLOSED = "closed"


@dataclass
class MCPToolDescriptor:
    local_name: str
    remote_name: str
    server_name: str
    description: str
    param_schema: dict
    allowed_agents: list[str]
    risk_level: str
    require_hitl: bool
    timeout_sec: int
    rate_limit_per_min: int
    capability: str = "tool"


@dataclass
class MCPResourceDescriptor:
    name: str
    uri: str
    server_name: str
    description: str = ""
    mime_type: str = ""


@dataclass
class MCPPromptDescriptor:
    name: str
    server_name: str
    description: str = ""
    arguments: list[dict] = field(default_factory=list)


@dataclass
class ServerRuntime:
    config: MCPServerConfig
    session: Any
    exit_stack: AsyncExitStack
    tool_names: list[str] = field(default_factory=list)
    resource_tool_names: list[str] = field(default_factory=list)
    prompt_tool_names: list[str] = field(default_factory=list)
    resource_descriptors: list[MCPResourceDescriptor] = field(default_factory=list)
    resource_templates: list[dict] = field(default_factory=list)
    prompt_descriptors: list[MCPPromptDescriptor] = field(default_factory=list)
    call_lock: asyncio.Lock | None = None
    semaphore: asyncio.Semaphore | None = None
    state: MCPServerState = MCPServerState.HEALTHY
    last_ping_at: float | None = None

    def __post_init__(self):
        if self.semaphore is None:
            self.semaphore = asyncio.Semaphore(1)
    consecutive_failures: int = 0
    reconnect_attempts: int = 0
    next_reconnect_at: float | None = None
    last_failure: str | None = None
