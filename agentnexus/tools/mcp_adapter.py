"""MCP client adapter: import external MCP capabilities into ToolRegistry."""

from __future__ import annotations

import asyncio
import inspect
import json
import re
import threading
import time
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import httpx

from agentnexus.core.config import MCPServerConfig
from agentnexus.tools.tool_executor import ToolExecutor

_NAME_SANITIZER = re.compile(r"[^a-zA-Z0-9_]+")


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
class _ServerRuntime:
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
    consecutive_failures: int = 0
    reconnect_attempts: int = 0
    next_reconnect_at: float | None = None
    last_failure: str | None = None


class MCPToolManager:
    def __init__(self, servers: list[MCPServerConfig], startup_timeout: int = 15):
        self._servers = [server for server in servers if server.enabled]
        self._startup_timeout = startup_timeout
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._started = False
        self._closing = False
        self._health_task: asyncio.Task | None = None
        self._tool_descriptors: dict[str, MCPToolDescriptor] = {}
        self._resource_descriptors: dict[str, list[MCPResourceDescriptor]] = {}
        self._resource_template_descriptors: dict[str, list[dict]] = {}
        self._prompt_descriptors: dict[str, list[MCPPromptDescriptor]] = {}
        self._server_runtimes: dict[str, _ServerRuntime] = {}
        self._server_states: dict[str, MCPServerState] = {
            server.name: MCPServerState.DISCONNECTED for server in self._servers
        }
        self._failures: dict[str, str] = {}
        self._registered_signatures: dict[tuple[int, str], str] = {}
        self._callable_cache: dict[str, Any] = {}

    @property
    def failures(self) -> dict[str, str]:
        return dict(self._failures)

    def status_snapshot(self) -> dict:
        servers = []
        total_resources = sum(len(items) for items in self._resource_descriptors.values())
        total_templates = sum(len(items) for items in self._resource_template_descriptors.values())
        total_prompts = sum(len(items) for items in self._prompt_descriptors.values())
        for server in self._servers:
            runtime = self._server_runtimes.get(server.name)
            state = self._runtime_state(server.name, runtime)
            resources = self._resource_descriptors.get(server.name, [])
            templates = self._resource_template_descriptors.get(server.name, [])
            prompts = self._prompt_descriptors.get(server.name, [])
            servers.append({
                "name": server.name,
                "transport": server.transport,
                "state": state.value,
                "connected": runtime is not None and state == MCPServerState.HEALTHY,
                "tool_names": sorted(list(getattr(runtime, "tool_names", []))) if runtime is not None else [],
                "resource_tool_names": (
                    sorted(list(getattr(runtime, "resource_tool_names", []))) if runtime is not None else []
                ),
                "prompt_tool_names": (
                    sorted(list(getattr(runtime, "prompt_tool_names", []))) if runtime is not None else []
                ),
                "resource_count": len(resources),
                "resource_template_count": len(templates),
                "prompt_count": len(prompts),
                "last_ping_at": getattr(runtime, "last_ping_at", None) if runtime is not None else None,
                "consecutive_failures": getattr(runtime, "consecutive_failures", 0) if runtime is not None else 0,
                "reconnect_attempts": getattr(runtime, "reconnect_attempts", 0) if runtime is not None else 0,
                "next_reconnect_at": getattr(runtime, "next_reconnect_at", None) if runtime is not None else None,
                "failure": self._failures.get(server.name),
            })
        return {
            "started": self._started,
            "server_count": len(self._servers),
            "connected_count": sum(
                1 for name, runtime in self._server_runtimes.items()
                if self._runtime_state(name, runtime) == MCPServerState.HEALTHY
            ),
            "failure_count": len(self._failures),
            "tool_count": len(self._tool_descriptors),
            "resource_count": total_resources,
            "resource_template_count": total_templates,
            "prompt_count": total_prompts,
            "servers": servers,
        }

    def auto_context(self) -> str:
        parts = []
        for server in self._servers:
            if not server.auto_context:
                continue
            lines = []
            resources = self._resource_descriptors.get(server.name, [])[:server.auto_context_max_items]
            prompts = self._prompt_descriptors.get(server.name, [])[:server.auto_context_max_items]
            templates = self._resource_template_descriptors.get(server.name, [])[:server.auto_context_max_items]
            if resources:
                lines.append("resources:")
                for item in resources:
                    desc = f" - {item.name or item.uri}: {item.uri}"
                    if item.description:
                        desc += f" - {item.description}"
                    lines.append(desc)
            if templates:
                lines.append("resource_templates:")
                for item in templates:
                    name = str(item.get("name") or item.get("uriTemplate") or item.get("uri_template") or "template")
                    desc = str(item.get("description") or "")
                    lines.append(f" - {name}{(': ' + desc) if desc else ''}")
            if prompts:
                lines.append("prompts:")
                for item in prompts:
                    desc = f" - {item.name}"
                    if item.description:
                        desc += f": {item.description}"
                    lines.append(desc)
            if lines:
                text = f"== MCP {server.name} capabilities ==\n" + "\n".join(lines)
                if server.auto_context_max_chars:
                    text = text[:server.auto_context_max_chars]
                parts.append(text)
        return "\n\n".join(parts)

    def retry_failed(self, server_name: str | None = None) -> dict:
        return self._submit(
            self._retry_failed_async(server_name=server_name),
            timeout=max(5, self._startup_timeout * max(1, len(self._servers))),
        )

    def start(self) -> None:
        if self._started:
            return
        if not self._servers:
            self._started = True
            return
        self._closing = False
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, name="agentnexus-mcp", daemon=True)
        self._thread.start()
        try:
            self._submit(self._connect_all(), timeout=max(5, self._startup_timeout * max(1, len(self._servers))))
            self._submit(self._start_health_loop(), timeout=5)
        except Exception:
            if self._loop is not None and self._thread is not None:
                self._loop.call_soon_threadsafe(self._loop.stop)
                self._thread.join(timeout=5)
            self._loop = None
            self._thread = None
            raise
        self._started = True

    def close(self) -> None:
        if not self._started:
            return
        self._closing = True
        if self._loop is not None and self._thread is not None:
            try:
                self._submit(self._close_all(), timeout=10)
            except Exception:
                pass
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(timeout=5)
        self._loop = None
        self._thread = None
        self._health_task = None
        self._started = False
        self._closing = False

    def tool_descriptors(self) -> list[MCPToolDescriptor]:
        return list(self._tool_descriptors.values())

    def list_tool_names(self) -> list[str]:
        return [tool.local_name for tool in self.tool_descriptors()]

    def list_subagent_tool_names(self) -> list[str]:
        names = []
        for tool in self.tool_descriptors():
            if "*" in tool.allowed_agents or any(agent.startswith("subagent_") for agent in tool.allowed_agents):
                names.append(tool.local_name)
        return names

    def register_tools(self, executor: ToolExecutor, include_tools: set[str] | None = None) -> list[str]:
        registered = []
        executor_key = id(executor.registry)
        for tool in self.tool_descriptors():
            if include_tools is not None and tool.local_name not in include_tools:
                continue
            signature = self._descriptor_signature(tool)
            cache_key = (executor_key, tool.local_name)
            if self._registered_signatures.get(cache_key) == signature:
                registered.append(tool.local_name)
                continue
            executor.registerTool(
                tool.local_name,
                tool.description,
                self._make_tool_callable(tool.local_name),
                param_schema=tool.param_schema,
                allowed_agents=tool.allowed_agents,
                risk_level=tool.risk_level,
                require_hitl=tool.require_hitl,
                timeout_sec=tool.timeout_sec,
                rate_limit_per_min=tool.rate_limit_per_min,
            )
            self._registered_signatures[cache_key] = signature
            registered.append(tool.local_name)
        return registered

    def call_tool(self, local_name: str, params: dict | None = None) -> str:
        descriptor = self._tool_descriptors.get(local_name)
        if descriptor is None:
            raise KeyError(f"Unknown MCP tool: {local_name}")
        return self._submit(
            self._call_descriptor_async(descriptor, params or {}),
            timeout=descriptor.timeout_sec + 5,
        )

    def _make_tool_callable(self, local_name: str):
        if local_name in self._callable_cache:
            return self._callable_cache[local_name]

        def _tool_callable(**params):
            return self.call_tool(local_name, params)

        _tool_callable.__name__ = f"mcp_{local_name}"
        self._callable_cache[local_name] = _tool_callable
        return _tool_callable

    def _run_loop(self) -> None:
        assert self._loop is not None
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()
        pending = asyncio.all_tasks(self._loop)
        for task in pending:
            task.cancel()
        if pending:
            self._loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        self._loop.close()

    def _submit(self, coro, timeout: int | float | None = None):
        if self._loop is None:
            raise RuntimeError("MCP manager event loop is not running")
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=timeout)

    async def _start_health_loop(self) -> None:
        if self._health_task is None or self._health_task.done():
            self._health_task = asyncio.create_task(self._health_loop())

    async def _health_loop(self) -> None:
        while not self._closing:
            await self._health_check_once()
            interval = min((server.health_check_interval_sec for server in self._servers), default=30)
            await asyncio.sleep(max(1, interval))

    async def _health_check_once(self) -> None:
        now = time.time()
        for server in self._servers:
            runtime = self._server_runtimes.get(server.name)
            if runtime is not None and runtime.state == MCPServerState.HEALTHY:
                if runtime.last_ping_at and now - runtime.last_ping_at < server.health_check_interval_sec:
                    continue
                try:
                    await asyncio.wait_for(runtime.session.send_ping(), timeout=min(server.timeout_sec, 10))
                    runtime.last_ping_at = time.time()
                    runtime.consecutive_failures = 0
                    runtime.last_failure = None
                    self._failures.pop(server.name, None)
                except Exception as exc:
                    runtime.state = MCPServerState.DEGRADED
                    runtime.consecutive_failures += 1
                    runtime.last_failure = str(exc)
                    self._server_states[server.name] = MCPServerState.DEGRADED
                    self._failures[server.name] = str(exc)
                    await self._schedule_reconnect(server, runtime)
            elif self._should_attempt_reconnect(server, runtime, now):
                await self._reconnect_server(server, runtime)

    async def _connect_all(self) -> None:
        self._ensure_sdk_available()
        for server in self._servers:
            self._server_states[server.name] = MCPServerState.CONNECTING
            try:
                await self._connect_server(server)
                self._failures.pop(server.name, None)
            except Exception as exc:
                self._server_states[server.name] = MCPServerState.DISCONNECTED
                self._failures[server.name] = str(exc)

    async def _retry_failed_async(self, server_name: str | None = None) -> dict:
        self._ensure_sdk_available()
        retried = []
        skipped = []
        reconnected = []
        failed = {}

        for server in self._servers:
            if server_name and server.name != server_name:
                continue
            runtime = self._server_runtimes.get(server.name)
            was_failed = server.name in self._failures
            is_connected = runtime is not None and self._runtime_state(server.name, runtime) == MCPServerState.HEALTHY
            should_retry = was_failed or not is_connected
            if not should_retry:
                skipped.append(server.name)
                continue

            retried.append(server.name)
            try:
                await self._reconnect_server(server, runtime, force=True)
                self._failures.pop(server.name, None)
                reconnected.append(server.name)
            except Exception as exc:
                self._failures[server.name] = str(exc)
                failed[server.name] = str(exc)

        return {
            "retried": retried,
            "skipped": skipped,
            "reconnected": reconnected,
            "failed": failed,
            "snapshot": self.status_snapshot(),
        }

    async def _connect_server(self, config: MCPServerConfig) -> None:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        from mcp.client.streamable_http import streamable_http_client

        stack = AsyncExitStack()
        self._server_states[config.name] = MCPServerState.CONNECTING
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
                kwargs = self._build_http_client_kwargs(streamable_http_client, config, http_client)
                transport_result = await stack.enter_async_context(streamable_http_client(**kwargs))
                if len(transport_result) >= 2:
                    read_stream, write_stream = transport_result[0], transport_result[1]
                else:
                    raise RuntimeError(f"Unexpected MCP HTTP transport result: {transport_result}")

            session = await stack.enter_async_context(ClientSession(read_stream, write_stream))
            await asyncio.wait_for(session.initialize(), timeout=self._startup_timeout)
            runtime = _ServerRuntime(
                config=config,
                session=session,
                exit_stack=stack,
                semaphore=asyncio.Semaphore(config.max_concurrency_per_server),
                call_lock=asyncio.Lock(),
                state=MCPServerState.HEALTHY,
                last_ping_at=time.time(),
            )
            self._server_runtimes[config.name] = runtime
            self._server_states[config.name] = MCPServerState.HEALTHY
            await self._import_server_capabilities(runtime)
            self._failures.pop(config.name, None)
        except Exception:
            self._server_runtimes.pop(config.name, None)
            self._clear_server_descriptors(config.name)
            await stack.aclose()
            self._server_states[config.name] = MCPServerState.DISCONNECTED
            raise

    async def _import_server_capabilities(self, runtime: _ServerRuntime) -> None:
        config = runtime.config
        self._clear_server_descriptors(config.name)
        if config.import_tools:
            tools_result = await asyncio.wait_for(runtime.session.list_tools(), timeout=self._startup_timeout)
            for tool in getattr(tools_result, "tools", []) or []:
                descriptor = self._build_descriptor(config, tool)
                if descriptor is None:
                    continue
                self._tool_descriptors[descriptor.local_name] = descriptor
                runtime.tool_names.append(descriptor.local_name)
        if config.import_resources:
            await self._import_resources(runtime)
        if config.import_prompts:
            await self._import_prompts(runtime)

    async def _import_resources(self, runtime: _ServerRuntime) -> None:
        config = runtime.config
        if not hasattr(runtime.session, "list_resources") and not hasattr(runtime.session, "list_resource_templates"):
            return
        resources: list[MCPResourceDescriptor] = []
        templates: list[dict] = []
        listed_any = False
        try:
            result = await asyncio.wait_for(runtime.session.list_resources(), timeout=self._startup_timeout)
            for item in getattr(result, "resources", []) or []:
                resources.append(_resource_descriptor_from_sdk(config.name, item))
            listed_any = True
        except Exception as exc:
            self._failures[config.name] = f"list_resources: {exc}"
        try:
            result = await asyncio.wait_for(runtime.session.list_resource_templates(), timeout=self._startup_timeout)
            for item in getattr(result, "resourceTemplates", None) or getattr(result, "resource_templates", []) or []:
                templates.append(_dump_sdk_object(item))
            listed_any = True
        except Exception as exc:
            if "list_resources" not in self._failures.get(config.name, ""):
                self._failures[config.name] = f"list_resource_templates: {exc}"
        self._resource_descriptors[config.name] = resources
        self._resource_template_descriptors[config.name] = templates
        runtime.resource_descriptors = resources
        runtime.resource_templates = templates
        if not listed_any or not hasattr(runtime.session, "read_resource"):
            return
        for descriptor in self._build_resource_tool_descriptors(config):
            self._tool_descriptors[descriptor.local_name] = descriptor
            runtime.resource_tool_names.append(descriptor.local_name)

    async def _import_prompts(self, runtime: _ServerRuntime) -> None:
        config = runtime.config
        if not hasattr(runtime.session, "list_prompts"):
            return
        prompts: list[MCPPromptDescriptor] = []
        listed = False
        try:
            result = await asyncio.wait_for(runtime.session.list_prompts(), timeout=self._startup_timeout)
            for item in getattr(result, "prompts", []) or []:
                prompts.append(_prompt_descriptor_from_sdk(config.name, item))
            listed = True
        except Exception as exc:
            self._failures[config.name] = f"list_prompts: {exc}"
        self._prompt_descriptors[config.name] = prompts
        runtime.prompt_descriptors = prompts
        if not listed or not hasattr(runtime.session, "get_prompt"):
            return
        for descriptor in self._build_prompt_tool_descriptors(config):
            self._tool_descriptors[descriptor.local_name] = descriptor
            runtime.prompt_tool_names.append(descriptor.local_name)

    def _build_resource_tool_descriptors(self, config: MCPServerConfig) -> list[MCPToolDescriptor]:
        prefix = _sanitize_name(config.tool_prefix or config.name)
        return [
            self._internal_descriptor(config, f"mcp_{prefix}__list_resources", "list_resources",
                                      f"[MCP:{config.name}] List available MCP resources.",
                                      {}, "resource"),
            self._internal_descriptor(config, f"mcp_{prefix}__read_resource", "read_resource",
                                      f"[MCP:{config.name}] Read an MCP resource by uri.",
                                      {"uri": {"type": "string"}}, "resource", ["uri"]),
            self._internal_descriptor(config, f"mcp_{prefix}__list_resource_templates", "list_resource_templates",
                                      f"[MCP:{config.name}] List MCP resource templates.",
                                      {}, "resource"),
        ]

    def _build_prompt_tool_descriptors(self, config: MCPServerConfig) -> list[MCPToolDescriptor]:
        prefix = _sanitize_name(config.tool_prefix or config.name)
        return [
            self._internal_descriptor(config, f"mcp_{prefix}__list_prompts", "list_prompts",
                                      f"[MCP:{config.name}] List available MCP prompts.",
                                      {}, "prompt"),
            self._internal_descriptor(config, f"mcp_{prefix}__get_prompt", "get_prompt",
                                      f"[MCP:{config.name}] Get an MCP prompt by name and optional arguments.",
                                      {
                                          "name": {"type": "string"},
                                          "arguments": {"type": "object", "additionalProperties": {"type": "string"}},
                                      },
                                      "prompt", ["name"]),
        ]

    def _internal_descriptor(
        self,
        config: MCPServerConfig,
        local_name: str,
        remote_name: str,
        description: str,
        properties: dict,
        capability: str,
        required: list[str] | None = None,
    ) -> MCPToolDescriptor:
        return MCPToolDescriptor(
            local_name=self._ensure_unique_name(local_name),
            remote_name=remote_name,
            server_name=config.name,
            description=description,
            param_schema={"type": "object", "properties": properties, "required": required or []},
            allowed_agents=list(config.allowed_agents),
            risk_level=config.risk_level,
            require_hitl=config.require_hitl,
            timeout_sec=config.timeout_sec,
            rate_limit_per_min=config.rate_limit_per_min,
            capability=capability,
        )

    @staticmethod
    def _build_http_client_kwargs(factory, config: MCPServerConfig, http_client: httpx.AsyncClient) -> dict:
        params = inspect.signature(factory).parameters
        kwargs = {}
        if "url" in params:
            kwargs["url"] = config.url
        else:
            kwargs["server_url"] = config.url
        if "http_client" in params:
            kwargs["http_client"] = http_client
        return kwargs

    def _build_descriptor(self, config: MCPServerConfig, tool: Any) -> MCPToolDescriptor | None:
        remote_name = getattr(tool, "name", None)
        if not remote_name:
            return None
        local_name = self._build_local_tool_name(config, remote_name)
        if not self._should_import_tool(config, remote_name, local_name):
            return None

        description = (getattr(tool, "description", "") or "").strip()
        if description:
            description = f"[MCP:{config.name}] {description}"
        else:
            description = f"[MCP:{config.name}] 远端工具 {remote_name}"

        return MCPToolDescriptor(
            local_name=self._ensure_unique_name(local_name),
            remote_name=remote_name,
            server_name=config.name,
            description=description,
            param_schema=self._normalize_param_schema(
                getattr(tool, "inputSchema", None) or getattr(tool, "input_schema", None)
            ),
            allowed_agents=list(config.allowed_agents),
            risk_level=config.risk_level,
            require_hitl=config.require_hitl,
            timeout_sec=config.timeout_sec,
            rate_limit_per_min=config.rate_limit_per_min,
        )

    def _build_local_tool_name(self, config: MCPServerConfig, remote_name: str) -> str:
        server_part = _sanitize_name(config.tool_prefix or config.name)
        tool_part = _sanitize_name(remote_name)
        return f"mcp_{server_part}__{tool_part}"

    def _ensure_unique_name(self, local_name: str) -> str:
        if local_name not in self._tool_descriptors:
            return local_name
        suffix = 2
        while f"{local_name}_{suffix}" in self._tool_descriptors:
            suffix += 1
        return f"{local_name}_{suffix}"

    @staticmethod
    def _normalize_param_schema(schema: dict | None) -> dict:
        if not isinstance(schema, dict):
            return {"type": "object", "properties": {}}
        normalized = dict(schema)
        normalized.setdefault("type", "object")
        normalized.setdefault("properties", {})
        return normalized

    @staticmethod
    def _should_import_tool(config: MCPServerConfig, remote_name: str, local_name: str) -> bool:
        includes = set(config.include_tools)
        excludes = set(config.exclude_tools)
        if includes and remote_name not in includes and local_name not in includes:
            return False
        if remote_name in excludes or local_name in excludes:
            return False
        return True

    async def _call_descriptor_async(self, descriptor: MCPToolDescriptor, params: dict) -> str:
        runtime = self._server_runtimes.get(descriptor.server_name)
        state = self._runtime_state(descriptor.server_name, runtime)
        if runtime is None or state not in {MCPServerState.HEALTHY, MCPServerState.DEGRADED}:
            raise RuntimeError(f"MCP server '{descriptor.server_name}' is not connected")
        limiter = getattr(runtime, "semaphore", None) or getattr(runtime, "call_lock", None)
        if limiter is None:
            limiter = asyncio.Semaphore(1)
            try:
                runtime.semaphore = limiter
            except Exception:
                pass
        async with limiter:
            try:
                if descriptor.capability == "tool":
                    result = await asyncio.wait_for(
                        runtime.session.call_tool(descriptor.remote_name, arguments=params),
                        timeout=descriptor.timeout_sec,
                    )
                    text = _normalize_tool_result(result)
                    if getattr(result, "isError", False) or getattr(result, "is_error", False):
                        raise RuntimeError(text or f"MCP tool '{descriptor.remote_name}' returned an error")
                    return text
                if descriptor.remote_name == "list_resources":
                    resources = self._resource_descriptors.get(descriptor.server_name, [])
                    return _json_text([item.__dict__ for item in resources])
                if descriptor.remote_name == "list_resource_templates":
                    return _json_text(self._resource_template_descriptors.get(descriptor.server_name, []))
                if descriptor.remote_name == "read_resource":
                    result = await asyncio.wait_for(runtime.session.read_resource(params["uri"]),
                                                    timeout=descriptor.timeout_sec)
                    return _normalize_resource_result(result)
                if descriptor.remote_name == "list_prompts":
                    prompts = self._prompt_descriptors.get(descriptor.server_name, [])
                    return _json_text([item.__dict__ for item in prompts])
                if descriptor.remote_name == "get_prompt":
                    result = await asyncio.wait_for(
                        runtime.session.get_prompt(params["name"], arguments=params.get("arguments") or None),
                        timeout=descriptor.timeout_sec,
                    )
                    return _normalize_prompt_result(result)
                raise RuntimeError(f"Unsupported MCP capability: {descriptor.remote_name}")
            except Exception as exc:
                await self._mark_runtime_failure(runtime, exc)
                raise

    async def _call_tool_async(self, descriptor: MCPToolDescriptor, params: dict) -> str:
        return await self._call_descriptor_async(descriptor, params)

    async def _mark_runtime_failure(self, runtime: _ServerRuntime, exc: Exception) -> None:
        server_name = getattr(getattr(runtime, "config", None), "name", None)
        try:
            runtime.consecutive_failures = getattr(runtime, "consecutive_failures", 0) + 1
            runtime.last_failure = str(exc)
            runtime.state = MCPServerState.DEGRADED
        except Exception:
            pass
        if server_name is None:
            return
        self._server_states[server_name] = MCPServerState.DEGRADED
        self._failures[server_name] = str(exc)
        await self._schedule_reconnect(runtime.config, runtime)

    async def _schedule_reconnect(self, server: MCPServerConfig, runtime: _ServerRuntime | None) -> None:
        if runtime is None:
            return
        attempts = runtime.reconnect_attempts
        if server.reconnect_max_attempts and attempts >= server.reconnect_max_attempts:
            return
        delay = min(server.reconnect_max_delay_sec, server.reconnect_initial_delay_sec * (2 ** attempts))
        runtime.reconnect_attempts += 1
        runtime.next_reconnect_at = time.time() + delay

    def _should_attempt_reconnect(
        self, server: MCPServerConfig, runtime: _ServerRuntime | None, now: float
    ) -> bool:
        if self._closing:
            return False
        if runtime is None:
            return server.name in self._failures
        next_reconnect_at = getattr(runtime, "next_reconnect_at", None)
        if next_reconnect_at is None:
            return False
        return now >= next_reconnect_at

    async def _reconnect_server(
        self, server: MCPServerConfig, runtime: _ServerRuntime | None = None, force: bool = False
    ) -> None:
        runtime = runtime or self._server_runtimes.get(server.name)
        if runtime is not None and not force and runtime.next_reconnect_at:
            if time.time() < runtime.next_reconnect_at:
                return
        self._server_states[server.name] = MCPServerState.RECONNECTING
        if runtime is not None:
            runtime.state = MCPServerState.RECONNECTING
        await self._disconnect_server(server.name)
        try:
            await self._connect_server(server)
        except Exception:
            self._server_states[server.name] = MCPServerState.DISCONNECTED
            raise

    async def _disconnect_server(self, server_name: str) -> None:
        runtime = self._server_runtimes.pop(server_name, None)
        self._clear_server_descriptors(server_name)
        if runtime is None:
            return
        runtime.state = MCPServerState.CLOSED
        await runtime.exit_stack.aclose()

    def _clear_server_descriptors(self, server_name: str) -> None:
        for name, descriptor in list(self._tool_descriptors.items()):
            if descriptor.server_name == server_name:
                self._tool_descriptors.pop(name, None)
                self._callable_cache.pop(name, None)
        self._resource_descriptors.pop(server_name, None)
        self._resource_template_descriptors.pop(server_name, None)
        self._prompt_descriptors.pop(server_name, None)

    async def _close_all(self) -> None:
        if self._health_task is not None:
            self._health_task.cancel()
            await asyncio.gather(self._health_task, return_exceptions=True)
            self._health_task = None
        runtimes = list(self._server_runtimes.values())
        self._server_runtimes.clear()
        self._tool_descriptors.clear()
        self._resource_descriptors.clear()
        self._resource_template_descriptors.clear()
        self._prompt_descriptors.clear()
        self._callable_cache.clear()
        for server in self._servers:
            self._server_states[server.name] = MCPServerState.CLOSED
        for runtime in runtimes:
            runtime.state = MCPServerState.CLOSED
            await runtime.exit_stack.aclose()

    def _runtime_state(self, server_name: str, runtime: Any) -> MCPServerState:
        state = getattr(runtime, "state", None)
        if isinstance(state, MCPServerState):
            return state
        if isinstance(state, str):
            try:
                return MCPServerState(state)
            except ValueError:
                pass
        configured = self._server_states.get(server_name)
        if runtime is not None and configured in {None, MCPServerState.DISCONNECTED}:
            return MCPServerState.HEALTHY
        return configured or MCPServerState.DISCONNECTED

    @staticmethod
    def _descriptor_signature(tool: MCPToolDescriptor) -> str:
        payload = {
            "description": tool.description,
            "schema": tool.param_schema,
            "allowed": tool.allowed_agents,
            "risk": tool.risk_level,
            "hitl": tool.require_hitl,
            "timeout": tool.timeout_sec,
            "rate": tool.rate_limit_per_min,
            "capability": tool.capability,
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)

    @staticmethod
    def _ensure_sdk_available() -> None:
        try:
            import mcp  # noqa: F401
        except ImportError as exc:
            raise RuntimeError("MCP SDK 未安装，请先安装依赖 'mcp'") from exc


def create_mcp_manager_from_settings(settings) -> MCPToolManager | None:
    if getattr(settings, "mcp_enabled", False) is not True:
        return None
    servers = list(getattr(settings, "mcp_servers", []) or [])
    enabled_servers = [server for server in servers if getattr(server, "enabled", True)]
    if not enabled_servers:
        return None
    manager = MCPToolManager(enabled_servers, startup_timeout=getattr(settings, "mcp_startup_timeout", 15))
    manager.start()
    return manager


def _sanitize_name(value: str) -> str:
    cleaned = _NAME_SANITIZER.sub("_", (value or "").strip().lower()).strip("_")
    return cleaned or "tool"


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _dump_sdk_object(value: Any) -> dict:
    if hasattr(value, "model_dump"):
        try:
            return value.model_dump(mode="json")
        except TypeError:
            return value.model_dump()
    if isinstance(value, dict):
        return dict(value)
    data = {}
    for attr in ("name", "uri", "uriTemplate", "uri_template", "description", "mimeType", "mime_type", "arguments"):
        if hasattr(value, attr):
            data[attr] = getattr(value, attr)
    return data or {"value": str(value)}


def _resource_descriptor_from_sdk(server_name: str, item: Any) -> MCPResourceDescriptor:
    data = _dump_sdk_object(item)
    return MCPResourceDescriptor(
        name=str(data.get("name") or data.get("uri") or ""),
        uri=str(data.get("uri") or ""),
        server_name=server_name,
        description=str(data.get("description") or ""),
        mime_type=str(data.get("mimeType") or data.get("mime_type") or ""),
    )


def _prompt_descriptor_from_sdk(server_name: str, item: Any) -> MCPPromptDescriptor:
    data = _dump_sdk_object(item)
    args = data.get("arguments") or []
    if not isinstance(args, list):
        args = []
    return MCPPromptDescriptor(
        name=str(data.get("name") or ""),
        server_name=server_name,
        description=str(data.get("description") or ""),
        arguments=[_dump_sdk_object(arg) for arg in args],
    )


def _normalize_tool_result(result: Any) -> str:
    parts: list[str] = []
    structured = getattr(result, "structuredContent", None)
    if structured is None:
        structured = getattr(result, "structured_content", None)
    if structured is not None:
        parts.append(json.dumps(structured, ensure_ascii=False, default=str))

    for block in getattr(result, "content", []) or []:
        text = _content_block_to_text(block)
        if text:
            parts.append(text)

    return "\n".join(part for part in parts if part).strip() or "[mcp] 工具未返回文本内容"


def _normalize_resource_result(result: Any) -> str:
    parts = []
    for block in getattr(result, "contents", None) or getattr(result, "content", []) or []:
        text = _content_block_to_text(block)
        if text:
            parts.append(text)
    if not parts and hasattr(result, "model_dump"):
        return _json_text(result.model_dump())
    return "\n".join(parts).strip() or "[mcp] 资源未返回文本内容"


def _normalize_prompt_result(result: Any) -> str:
    messages = getattr(result, "messages", []) or []
    if messages:
        return _json_text([_dump_sdk_object(message) for message in messages])
    if hasattr(result, "model_dump"):
        return _json_text(result.model_dump())
    return str(result)


def _content_block_to_text(block: Any) -> str:
    text = getattr(block, "text", None)
    if text:
        return str(text)

    resource = getattr(block, "resource", None)
    if resource is not None:
        resource_text = getattr(resource, "text", None)
        if resource_text:
            return str(resource_text)
        blob = getattr(resource, "blob", None)
        mime_type = getattr(resource, "mimeType", None) or getattr(resource, "mime_type", None) or "unknown"
        if blob is not None:
            return f"[embedded resource: {mime_type}]"
        uri = getattr(resource, "uri", None)
        if uri:
            return f"[embedded resource] {uri}"

    mime_type = getattr(block, "mimeType", None) or getattr(block, "mime_type", None)
    data = getattr(block, "data", None)
    if mime_type and data is not None:
        return f"[binary content: {mime_type}]"

    if hasattr(block, "model_dump"):
        try:
            return json.dumps(block.model_dump(), ensure_ascii=False, default=str)
        except Exception:
            return str(block)
    return str(block)
