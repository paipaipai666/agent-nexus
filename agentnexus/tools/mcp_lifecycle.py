"""Connection lifecycle orchestration for MCP servers."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from typing import Any

import httpx

from agentnexus.core.config import MCPServerConfig
from agentnexus.tools import mcp_connection
from agentnexus.tools.mcp_schema import MCPServerState, ServerRuntime

CapabilityImporter = Callable[[ServerRuntime], Awaitable[None]]
DescriptorClearer = Callable[[str], None]


async def connect_all(
    servers: list[MCPServerConfig],
    *,
    connect_server: Callable[[MCPServerConfig], Awaitable[None]],
    server_states: dict[str, MCPServerState],
    failures: dict[str, str],
) -> None:
    for server in servers:
        server_states[server.name] = MCPServerState.CONNECTING
        try:
            await connect_server(server)
            failures.pop(server.name, None)
        except Exception as exc:
            server_states[server.name] = MCPServerState.DISCONNECTED
            failures[server.name] = str(exc)


async def connect_server(
    config: MCPServerConfig,
    *,
    startup_timeout: int,
    server_runtimes: dict[str, ServerRuntime],
    server_states: dict[str, MCPServerState],
    failures: dict[str, str],
    import_capabilities: CapabilityImporter,
    clear_descriptors: DescriptorClearer,
) -> None:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from mcp.client.streamable_http import streamable_http_client

    stack = AsyncExitStack()
    server_states[config.name] = MCPServerState.CONNECTING
    try:
        if config.transport == "stdio":
            server_params = StdioServerParameters(
                command=config.command,
                args=config.args,
                env=config.env or None,
                cwd=config.cwd,
            )
            read_stream, write_stream = await stack.enter_async_context(stdio_client(server_params))
        else:
            http_client = await stack.enter_async_context(
                httpx.AsyncClient(headers=config.headers or None, timeout=config.timeout_sec)
            )
            kwargs = mcp_connection.build_http_client_kwargs(streamable_http_client, config, http_client)
            transport_result = await stack.enter_async_context(streamable_http_client(**kwargs))
            if len(transport_result) >= 2:
                read_stream, write_stream = transport_result[0], transport_result[1]
            else:
                raise RuntimeError(f"Unexpected MCP HTTP transport result: {transport_result}")

        session = await stack.enter_async_context(ClientSession(read_stream, write_stream))
        await asyncio.wait_for(session.initialize(), timeout=startup_timeout)
        runtime = ServerRuntime(
            config=config,
            session=session,
            exit_stack=stack,
            semaphore=asyncio.Semaphore(config.max_concurrency_per_server),
            call_lock=asyncio.Lock(),
            state=MCPServerState.HEALTHY,
            last_ping_at=time.time(),
        )
        server_runtimes[config.name] = runtime
        server_states[config.name] = MCPServerState.HEALTHY
        await import_capabilities(runtime)
        failures.pop(config.name, None)
    except Exception:
        server_runtimes.pop(config.name, None)
        clear_descriptors(config.name)
        await stack.aclose()
        server_states[config.name] = MCPServerState.DISCONNECTED
        raise


async def disconnect_server(
    server_name: str,
    *,
    server_runtimes: dict[str, ServerRuntime],
    clear_descriptors: DescriptorClearer,
) -> None:
    runtime = server_runtimes.pop(server_name, None)
    clear_descriptors(server_name)
    if runtime is None:
        return
    runtime.state = MCPServerState.CLOSED
    await runtime.exit_stack.aclose()


async def close_all(
    *,
    servers: list[MCPServerConfig],
    health_task: asyncio.Task | None,
    server_runtimes: dict[str, ServerRuntime],
    server_states: dict[str, MCPServerState],
    tool_descriptors: dict[str, Any],
    resource_descriptors: dict[str, list],
    resource_template_descriptors: dict[str, list[dict]],
    prompt_descriptors: dict[str, list],
    callable_cache: dict[str, Any],
) -> asyncio.Task | None:
    if health_task is not None:
        health_task.cancel()
        await asyncio.gather(health_task, return_exceptions=True)
        health_task = None
    runtimes = list(server_runtimes.values())
    server_runtimes.clear()
    tool_descriptors.clear()
    resource_descriptors.clear()
    resource_template_descriptors.clear()
    prompt_descriptors.clear()
    callable_cache.clear()
    for server in servers:
        server_states[server.name] = MCPServerState.CLOSED
    for runtime in runtimes:
        runtime.state = MCPServerState.CLOSED
        await runtime.exit_stack.aclose()
    return health_task
