"""MCP client adapter — import external MCP tools into ToolRegistry."""

from __future__ import annotations

import asyncio
import inspect
import json
import re
import threading
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any

import httpx

from agentnexus.core.config import MCPServerConfig
from agentnexus.tools.tool_executor import ToolExecutor

_NAME_SANITIZER = re.compile(r"[^a-zA-Z0-9_]+")


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


@dataclass
class _ServerRuntime:
    config: MCPServerConfig
    session: Any
    exit_stack: AsyncExitStack
    tool_names: list[str] = field(default_factory=list)
    call_lock: asyncio.Lock | None = None


class MCPToolManager:
    def __init__(self, servers: list[MCPServerConfig], startup_timeout: int = 15):
        self._servers = [server for server in servers if server.enabled]
        self._startup_timeout = startup_timeout
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._started = False
        self._tool_descriptors: dict[str, MCPToolDescriptor] = {}
        self._server_runtimes: dict[str, _ServerRuntime] = {}
        self._failures: dict[str, str] = {}

    @property
    def failures(self) -> dict[str, str]:
        return dict(self._failures)

    def status_snapshot(self) -> dict:
        servers = []
        for server in self._servers:
            runtime = self._server_runtimes.get(server.name)
            servers.append({
                "name": server.name,
                "transport": server.transport,
                "connected": runtime is not None,
                "tool_names": sorted(list(runtime.tool_names)) if runtime is not None else [],
                "failure": self._failures.get(server.name),
            })
        return {
            "started": self._started,
            "server_count": len(self._servers),
            "connected_count": len(self._server_runtimes),
            "failure_count": len(self._failures),
            "tool_count": len(self._tool_descriptors),
            "servers": servers,
        }

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
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, name="agentnexus-mcp", daemon=True)
        self._thread.start()
        try:
            self._submit(self._connect_all(), timeout=max(5, self._startup_timeout * max(1, len(self._servers))))
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
        if self._loop is not None and self._thread is not None:
            try:
                self._submit(self._close_all(), timeout=10)
            except Exception:
                pass
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(timeout=5)
        self._loop = None
        self._thread = None
        self._started = False

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
        for tool in self.tool_descriptors():
            if include_tools is not None and tool.local_name not in include_tools:
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
            registered.append(tool.local_name)
        return registered

    def call_tool(self, local_name: str, params: dict | None = None) -> str:
        descriptor = self._tool_descriptors.get(local_name)
        if descriptor is None:
            raise KeyError(f"Unknown MCP tool: {local_name}")
        return self._submit(
            self._call_tool_async(descriptor, params or {}),
            timeout=descriptor.timeout_sec + 5,
        )

    def _make_tool_callable(self, local_name: str):
        def _tool_callable(**params):
            return self.call_tool(local_name, params)

        _tool_callable.__name__ = f"mcp_{local_name}"
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

    async def _connect_all(self) -> None:
        self._ensure_sdk_available()
        for server in self._servers:
            try:
                await self._connect_server(server)
            except Exception as exc:
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
            was_failed = server.name in self._failures
            is_connected = server.name in self._server_runtimes
            should_retry = was_failed or not is_connected
            if not should_retry:
                skipped.append(server.name)
                continue

            retried.append(server.name)
            await self._disconnect_server(server.name)
            try:
                await self._connect_server(server)
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
            tools_result = await asyncio.wait_for(session.list_tools(), timeout=self._startup_timeout)
            runtime = _ServerRuntime(
                config=config,
                session=session,
                exit_stack=stack,
                call_lock=asyncio.Lock(),
            )
            self._server_runtimes[config.name] = runtime

            for tool in getattr(tools_result, "tools", []) or []:
                descriptor = self._build_descriptor(config, tool)
                if descriptor is None:
                    continue
                self._tool_descriptors[descriptor.local_name] = descriptor
                runtime.tool_names.append(descriptor.local_name)
        except Exception:
            await stack.aclose()
            raise

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

    async def _call_tool_async(self, descriptor: MCPToolDescriptor, params: dict) -> str:
        runtime = self._server_runtimes.get(descriptor.server_name)
        if runtime is None:
            raise RuntimeError(f"MCP server '{descriptor.server_name}' is not connected")
        assert runtime.call_lock is not None
        async with runtime.call_lock:
            result = await asyncio.wait_for(
                runtime.session.call_tool(descriptor.remote_name, arguments=params),
                timeout=descriptor.timeout_sec,
            )
        text = _normalize_tool_result(result)
        if getattr(result, "isError", False) or getattr(result, "is_error", False):
            raise RuntimeError(text or f"MCP tool '{descriptor.remote_name}' returned an error")
        return text

    async def _disconnect_server(self, server_name: str) -> None:
        runtime = self._server_runtimes.pop(server_name, None)
        if runtime is None:
            return
        for tool_name in list(runtime.tool_names):
            self._tool_descriptors.pop(tool_name, None)
        await runtime.exit_stack.aclose()

    async def _close_all(self) -> None:
        runtimes = list(self._server_runtimes.values())
        self._server_runtimes.clear()
        self._tool_descriptors.clear()
        for runtime in runtimes:
            await runtime.exit_stack.aclose()

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
