"""MCP client adapter: import external MCP capabilities into ToolRegistry."""

from __future__ import annotations

import asyncio
import json
import re
import threading
import time
from typing import Any

import httpx

from agentnexus.core.config import MCPServerConfig
from agentnexus.tools import (
    mcp_call,
    mcp_capabilities,
    mcp_connection,
    mcp_descriptors,
    mcp_health,
    mcp_lifecycle,
    mcp_result,
)
from agentnexus.tools.mcp_schema import (
    MCPPromptDescriptor,
    MCPResourceDescriptor,
    MCPServerState,
    MCPToolDescriptor,
    ServerRuntime,
)
from agentnexus.tools.tool_executor import ToolExecutor

_NAME_SANITIZER = re.compile(r"[^a-zA-Z0-9_]+")


_ServerRuntime = ServerRuntime


class MCPToolManager:
    def __init__(self, servers: list[MCPServerConfig], startup_timeout: int = 15):
        self._all_servers = list(servers)
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

    def server_names(self) -> list[str]:
        return [server.name for server in self._all_servers]

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
        if not self._all_servers:
            self._started = True
            return
        self._closing = False
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, name="agentnexus-mcp", daemon=True)
        self._thread.start()
        try:
            if self._servers:
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

    def enable_server(self, server_name: str) -> dict:
        if not self._started or self._loop is None:
            self.start()
        return self._submit(self._enable_server_async(server_name), timeout=max(5, self._startup_timeout))

    def disable_server(self, server_name: str) -> dict:
        if not self._started or self._loop is None:
            self.start()
        return self._submit(self._disable_server_async(server_name), timeout=10)

    def reload_server(self, server_name: str | None = None) -> dict:
        if server_name is None:
            results = {}
            for name in self.server_names():
                results[name] = self.reload_server(name)
            return results
        return self._submit(self._reload_server_async(server_name), timeout=max(5, self._startup_timeout))

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
                source_type="mcp",
                source_id=f"mcp:{tool.server_name}",
            )
            self._registered_signatures[cache_key] = signature
            registered.append(tool.local_name)
        return registered

    def call_tool(self, local_name: str, params: dict | None = None) -> str:
        from agentnexus.core.hooks import HookType, get_hook_manager

        hook_mgr = get_hook_manager()
        hook_mgr.fire(HookType.BEFORE_MCP_CALL_TOOL, {
            "local_name": local_name, "params": params,
        })

        descriptor = self._tool_descriptors.get(local_name)
        if descriptor is None:
            raise KeyError(f"Unknown MCP tool: {local_name}")

        try:
            result = self._submit(
                self._call_descriptor_async(descriptor, params or {}),
                timeout=descriptor.timeout_sec + 5,
            )
            hook_mgr.fire(HookType.AFTER_MCP_CALL_TOOL, {
                "local_name": local_name, "server_name": descriptor.server_name,
                "result": str(result)[:500],
            })
            return result
        except Exception as exc:
            hook_mgr.fire(HookType.AFTER_MCP_CALL_TOOL, {
                "local_name": local_name, "server_name": descriptor.server_name,
                "error": str(exc),
            })
            raise

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
                    mcp_health.mark_runtime_healthy(runtime)
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
        await mcp_lifecycle.connect_all(
            self._servers,
            connect_server=self._connect_server,
            server_states=self._server_states,
            failures=self._failures,
        )

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

    async def _enable_server_async(self, server_name: str) -> dict:
        config = self._find_server_config(server_name)
        if config is None:
            raise KeyError(f"Unknown MCP server: {server_name}")
        if config not in self._servers:
            self._servers.append(config)
        if self._loop is None:
            self._ensure_sdk_available()
        runtime = self._server_runtimes.get(config.name)
        if runtime is None:
            await self._connect_server(config)
        return {"enabled": config.name, "snapshot": self.status_snapshot()}

    async def _disable_server_async(self, server_name: str) -> dict:
        config = self._find_server_config(server_name)
        if config is None:
            raise KeyError(f"Unknown MCP server: {server_name}")
        runtime = self._server_runtimes.pop(config.name, None)
        if runtime is not None:
            await runtime.exit_stack.aclose()
        self._servers = [server for server in self._servers if server.name != config.name]
        self._clear_server_descriptors(config.name)
        self._server_states[config.name] = MCPServerState.DISCONNECTED
        self._failures.pop(config.name, None)
        return {"disabled": config.name, "snapshot": self.status_snapshot()}

    async def _reload_server_async(self, server_name: str) -> dict:
        await self._disable_server_async(server_name)
        return await self._enable_server_async(server_name)

    def _find_server_config(self, server_name: str) -> MCPServerConfig | None:
        for server in self._all_servers:
            if server.name == server_name:
                return server
        return None

    async def _connect_server(self, config: MCPServerConfig) -> None:
        await mcp_lifecycle.connect_server(
            config,
            startup_timeout=self._startup_timeout,
            server_runtimes=self._server_runtimes,
            server_states=self._server_states,
            failures=self._failures,
            import_capabilities=self._import_server_capabilities,
            clear_descriptors=self._clear_server_descriptors,
        )

    async def _import_server_capabilities(self, runtime: _ServerRuntime) -> None:
        await mcp_capabilities.import_server_capabilities(
            runtime,
            startup_timeout=self._startup_timeout,
            tool_descriptors=self._tool_descriptors,
            resource_descriptors=self._resource_descriptors,
            resource_template_descriptors=self._resource_template_descriptors,
            prompt_descriptors=self._prompt_descriptors,
            failures=self._failures,
            clear_descriptors=self._clear_server_descriptors,
            build_descriptor=self._build_descriptor,
            ensure_unique_name=self._ensure_unique_name,
        )

    async def _import_resources(self, runtime: _ServerRuntime) -> None:
        await mcp_capabilities.import_resources(
            runtime,
            startup_timeout=self._startup_timeout,
            tool_descriptors=self._tool_descriptors,
            resource_descriptors=self._resource_descriptors,
            resource_template_descriptors=self._resource_template_descriptors,
            failures=self._failures,
            ensure_unique_name=self._ensure_unique_name,
        )

    async def _import_prompts(self, runtime: _ServerRuntime) -> None:
        await mcp_capabilities.import_prompts(
            runtime,
            startup_timeout=self._startup_timeout,
            tool_descriptors=self._tool_descriptors,
            prompt_descriptors=self._prompt_descriptors,
            failures=self._failures,
            ensure_unique_name=self._ensure_unique_name,
        )

    def _build_resource_tool_descriptors(self, config: MCPServerConfig) -> list[MCPToolDescriptor]:
        return mcp_capabilities.build_resource_tool_descriptors(
            config,
            ensure_unique_name=self._ensure_unique_name,
        )

    def _build_prompt_tool_descriptors(self, config: MCPServerConfig) -> list[MCPToolDescriptor]:
        return mcp_capabilities.build_prompt_tool_descriptors(
            config,
            ensure_unique_name=self._ensure_unique_name,
        )

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
        return mcp_capabilities._internal_descriptor(
            config,
            self._ensure_unique_name(local_name),
            remote_name,
            description,
            properties,
            capability,
            required,
        )

    @staticmethod
    def _build_http_client_kwargs(factory, config: MCPServerConfig, http_client: httpx.AsyncClient) -> dict:
        return mcp_connection.build_http_client_kwargs(factory, config, http_client)

    def _build_descriptor(self, config: MCPServerConfig, tool: Any) -> MCPToolDescriptor | None:
        remote_name = getattr(tool, "name", None)
        if not remote_name:
            return None
        local_name = self._build_local_tool_name(config, remote_name)
        if not self._should_import_tool(config, remote_name, local_name):
            return None
        return mcp_descriptors.build_tool_descriptor(
            config,
            tool,
            local_name=self._ensure_unique_name(local_name),
        )

    def _build_local_tool_name(self, config: MCPServerConfig, remote_name: str) -> str:
        return mcp_descriptors.build_local_tool_name(config, remote_name)

    def _ensure_unique_name(self, local_name: str) -> str:
        if local_name not in self._tool_descriptors:
            return local_name
        suffix = 2
        while f"{local_name}_{suffix}" in self._tool_descriptors:
            suffix += 1
        return f"{local_name}_{suffix}"

    @staticmethod
    def _normalize_param_schema(schema: dict | None) -> dict:
        return mcp_descriptors.normalize_param_schema(schema)

    @staticmethod
    def _should_import_tool(config: MCPServerConfig, remote_name: str, local_name: str) -> bool:
        return mcp_descriptors.should_import_tool(config, remote_name, local_name)

    async def _call_descriptor_async(self, descriptor: MCPToolDescriptor, params: dict) -> str:
        runtime = self._server_runtimes.get(descriptor.server_name)
        state = self._runtime_state(descriptor.server_name, runtime)
        if runtime is None or state not in {MCPServerState.HEALTHY, MCPServerState.DEGRADED}:
            raise RuntimeError(f"MCP server '{descriptor.server_name}' is not connected")
        try:
            return await mcp_call.run_with_limiter(
                runtime,
                lambda: mcp_call.call_descriptor(
                    runtime,
                    descriptor,
                    params,
                    resource_descriptors=self._resource_descriptors,
                    resource_template_descriptors=self._resource_template_descriptors,
                    prompt_descriptors=self._prompt_descriptors,
                ),
            )
        except Exception as exc:
            await self._mark_runtime_failure(runtime, exc)
            raise

    async def _call_tool_async(self, descriptor: MCPToolDescriptor, params: dict) -> str:
        return await self._call_descriptor_async(descriptor, params)

    async def _mark_runtime_failure(self, runtime: _ServerRuntime, exc: Exception) -> None:
        server_name, failure = mcp_health.mark_runtime_failure(runtime, exc)
        if server_name is None:
            return
        self._server_states[server_name] = MCPServerState.DEGRADED
        self._failures[server_name] = failure
        await self._schedule_reconnect(runtime.config, runtime)

    async def _schedule_reconnect(self, server: MCPServerConfig, runtime: _ServerRuntime | None) -> None:
        mcp_health.schedule_reconnect(server, runtime)

    def _should_attempt_reconnect(
        self, server: MCPServerConfig, runtime: _ServerRuntime | None, now: float
    ) -> bool:
        return mcp_health.should_attempt_reconnect(
            server,
            runtime,
            now,
            closing=self._closing,
            has_failure=server.name in self._failures,
        )

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
        await mcp_lifecycle.disconnect_server(
            server_name,
            server_runtimes=self._server_runtimes,
            clear_descriptors=self._clear_server_descriptors,
        )

    def _clear_server_descriptors(self, server_name: str) -> None:
        for name, descriptor in list(self._tool_descriptors.items()):
            if descriptor.server_name == server_name:
                self._tool_descriptors.pop(name, None)
                self._callable_cache.pop(name, None)
        self._resource_descriptors.pop(server_name, None)
        self._resource_template_descriptors.pop(server_name, None)
        self._prompt_descriptors.pop(server_name, None)

    async def _close_all(self) -> None:
        self._health_task = await mcp_lifecycle.close_all(
            servers=self._servers,
            health_task=self._health_task,
            server_runtimes=self._server_runtimes,
            server_states=self._server_states,
            tool_descriptors=self._tool_descriptors,
            resource_descriptors=self._resource_descriptors,
            resource_template_descriptors=self._resource_template_descriptors,
            prompt_descriptors=self._prompt_descriptors,
            callable_cache=self._callable_cache,
        )

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
        return mcp_connection.ensure_sdk_available()


def create_mcp_manager_from_settings(settings) -> MCPToolManager | None:
    if getattr(settings, "mcp_enabled", False) is not True:
        return None
    servers = _apply_capability_mcp_enabled(list(getattr(settings, "mcp_servers", []) or []))
    if not servers:
        return None
    manager = MCPToolManager(servers, startup_timeout=getattr(settings, "mcp_startup_timeout", 15))
    manager.start()
    return manager


def _apply_capability_mcp_enabled(servers: list[MCPServerConfig]) -> list[MCPServerConfig]:
    try:
        from agentnexus.core.config import load_config_yaml

        data = load_config_yaml()
        capabilities = data.get("capabilities") if isinstance(data, dict) else {}
        enabled_map = capabilities.get("mcp_servers") if isinstance(capabilities, dict) else {}
        enabled_map = enabled_map if isinstance(enabled_map, dict) else {}
    except Exception:
        enabled_map = {}

    result = []
    for server in servers:
        enabled = bool(enabled_map.get(server.name, False))
        if hasattr(server, "model_copy"):
            result.append(server.model_copy(update={"enabled": enabled}))
        else:
            server.enabled = enabled
            result.append(server)
    return result


def _sanitize_name(value: str) -> str:
    return mcp_result.sanitize_name(value)


def _json_text(value: Any) -> str:
    return mcp_result.json_text(value)


def _dump_sdk_object(value: Any) -> dict:
    return mcp_result.dump_sdk_object(value)


def _resource_descriptor_from_sdk(server_name: str, item: Any) -> MCPResourceDescriptor:
    return mcp_descriptors.resource_descriptor_from_sdk(server_name, item)


def _prompt_descriptor_from_sdk(server_name: str, item: Any) -> MCPPromptDescriptor:
    return mcp_descriptors.prompt_descriptor_from_sdk(server_name, item)


def _normalize_tool_result(result: Any) -> str:
    return mcp_result.normalize_tool_result(result)


def _normalize_resource_result(result: Any) -> str:
    return mcp_result.normalize_resource_result(result)


def _normalize_prompt_result(result: Any) -> str:
    return mcp_result.normalize_prompt_result(result)


def _content_block_to_text(block: Any) -> str:
    return mcp_result.content_block_to_text(block)
