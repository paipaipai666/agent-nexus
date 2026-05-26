"""MCP capability import and internal descriptor construction."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from agentnexus.core.config import MCPServerConfig
from agentnexus.tools import mcp_descriptors, mcp_result
from agentnexus.tools.mcp_schema import (
    MCPPromptDescriptor,
    MCPResourceDescriptor,
    MCPToolDescriptor,
    ServerRuntime,
)

UniqueName = Callable[[str], str]
BuildDescriptor = Callable[[MCPServerConfig, Any], MCPToolDescriptor | None]
ClearDescriptors = Callable[[str], None]


async def import_server_capabilities(
    runtime: ServerRuntime,
    *,
    startup_timeout: int,
    tool_descriptors: dict[str, MCPToolDescriptor],
    resource_descriptors: dict[str, list[MCPResourceDescriptor]],
    resource_template_descriptors: dict[str, list[dict]],
    prompt_descriptors: dict[str, list[MCPPromptDescriptor]],
    failures: dict[str, str],
    clear_descriptors: ClearDescriptors,
    build_descriptor: BuildDescriptor,
    ensure_unique_name: UniqueName,
) -> None:
    config = runtime.config
    clear_descriptors(config.name)
    if config.import_tools:
        tools_result = await asyncio.wait_for(runtime.session.list_tools(), timeout=startup_timeout)
        for tool in getattr(tools_result, "tools", []) or []:
            descriptor = build_descriptor(config, tool)
            if descriptor is None:
                continue
            tool_descriptors[descriptor.local_name] = descriptor
            runtime.tool_names.append(descriptor.local_name)
    if config.import_resources:
        await import_resources(
            runtime,
            startup_timeout=startup_timeout,
            tool_descriptors=tool_descriptors,
            resource_descriptors=resource_descriptors,
            resource_template_descriptors=resource_template_descriptors,
            failures=failures,
            ensure_unique_name=ensure_unique_name,
        )
    if config.import_prompts:
        await import_prompts(
            runtime,
            startup_timeout=startup_timeout,
            tool_descriptors=tool_descriptors,
            prompt_descriptors=prompt_descriptors,
            failures=failures,
            ensure_unique_name=ensure_unique_name,
        )


async def import_resources(
    runtime: ServerRuntime,
    *,
    startup_timeout: int,
    tool_descriptors: dict[str, MCPToolDescriptor],
    resource_descriptors: dict[str, list[MCPResourceDescriptor]],
    resource_template_descriptors: dict[str, list[dict]],
    failures: dict[str, str],
    ensure_unique_name: UniqueName,
) -> None:
    config = runtime.config
    if not hasattr(runtime.session, "list_resources") and not hasattr(runtime.session, "list_resource_templates"):
        return
    resources: list[MCPResourceDescriptor] = []
    templates: list[dict] = []
    listed_any = False
    try:
        result = await asyncio.wait_for(runtime.session.list_resources(), timeout=startup_timeout)
        for item in getattr(result, "resources", []) or []:
            resources.append(mcp_descriptors.resource_descriptor_from_sdk(config.name, item))
        listed_any = True
    except Exception as exc:
        failures[config.name] = f"list_resources: {exc}"
    try:
        result = await asyncio.wait_for(runtime.session.list_resource_templates(), timeout=startup_timeout)
        for item in getattr(result, "resourceTemplates", None) or getattr(result, "resource_templates", []) or []:
            templates.append(mcp_result.dump_sdk_object(item))
        listed_any = True
    except Exception as exc:
        if "list_resources" not in failures.get(config.name, ""):
            failures[config.name] = f"list_resource_templates: {exc}"
    resource_descriptors[config.name] = resources
    resource_template_descriptors[config.name] = templates
    runtime.resource_descriptors = resources
    runtime.resource_templates = templates
    if not listed_any or not hasattr(runtime.session, "read_resource"):
        return
    for descriptor in build_resource_tool_descriptors(config, ensure_unique_name=ensure_unique_name):
        tool_descriptors[descriptor.local_name] = descriptor
        runtime.resource_tool_names.append(descriptor.local_name)


async def import_prompts(
    runtime: ServerRuntime,
    *,
    startup_timeout: int,
    tool_descriptors: dict[str, MCPToolDescriptor],
    prompt_descriptors: dict[str, list[MCPPromptDescriptor]],
    failures: dict[str, str],
    ensure_unique_name: UniqueName,
) -> None:
    config = runtime.config
    if not hasattr(runtime.session, "list_prompts"):
        return
    prompts: list[MCPPromptDescriptor] = []
    listed = False
    try:
        result = await asyncio.wait_for(runtime.session.list_prompts(), timeout=startup_timeout)
        for item in getattr(result, "prompts", []) or []:
            prompts.append(mcp_descriptors.prompt_descriptor_from_sdk(config.name, item))
        listed = True
    except Exception as exc:
        failures[config.name] = f"list_prompts: {exc}"
    prompt_descriptors[config.name] = prompts
    runtime.prompt_descriptors = prompts
    if not listed or not hasattr(runtime.session, "get_prompt"):
        return
    for descriptor in build_prompt_tool_descriptors(config, ensure_unique_name=ensure_unique_name):
        tool_descriptors[descriptor.local_name] = descriptor
        runtime.prompt_tool_names.append(descriptor.local_name)


def build_resource_tool_descriptors(
    config: MCPServerConfig,
    *,
    ensure_unique_name: UniqueName = lambda name: name,
) -> list[MCPToolDescriptor]:
    prefix = mcp_result.sanitize_name(config.tool_prefix or config.name)
    return [
        _internal_descriptor(
            config,
            ensure_unique_name(f"mcp_{prefix}__list_resources"),
            "list_resources",
            f"[MCP:{config.name}] List available MCP resources.",
            {},
            "resource",
        ),
        _internal_descriptor(
            config,
            ensure_unique_name(f"mcp_{prefix}__read_resource"),
            "read_resource",
            f"[MCP:{config.name}] Read an MCP resource by uri.",
            {"uri": {"type": "string"}},
            "resource",
            ["uri"],
        ),
        _internal_descriptor(
            config,
            ensure_unique_name(f"mcp_{prefix}__list_resource_templates"),
            "list_resource_templates",
            f"[MCP:{config.name}] List MCP resource templates.",
            {},
            "resource",
        ),
    ]


def build_prompt_tool_descriptors(
    config: MCPServerConfig,
    *,
    ensure_unique_name: UniqueName = lambda name: name,
) -> list[MCPToolDescriptor]:
    prefix = mcp_result.sanitize_name(config.tool_prefix or config.name)
    return [
        _internal_descriptor(
            config,
            ensure_unique_name(f"mcp_{prefix}__list_prompts"),
            "list_prompts",
            f"[MCP:{config.name}] List available MCP prompts.",
            {},
            "prompt",
        ),
        _internal_descriptor(
            config,
            ensure_unique_name(f"mcp_{prefix}__get_prompt"),
            "get_prompt",
            f"[MCP:{config.name}] Get an MCP prompt by name and optional arguments.",
            {
                "name": {"type": "string"},
                "arguments": {"type": "object", "additionalProperties": {"type": "string"}},
            },
            "prompt",
            ["name"],
        ),
    ]


def _internal_descriptor(
    config: MCPServerConfig,
    local_name: str,
    remote_name: str,
    description: str,
    properties: dict,
    capability: str,
    required: list[str] | None = None,
) -> MCPToolDescriptor:
    return MCPToolDescriptor(
        local_name=local_name,
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

