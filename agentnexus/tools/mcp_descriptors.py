"""Descriptor construction helpers for MCP capabilities."""

from __future__ import annotations

from typing import Any

from agentnexus.core.config import MCPServerConfig
from agentnexus.tools.mcp_result import dump_sdk_object, sanitize_name
from agentnexus.tools.mcp_schema import MCPPromptDescriptor, MCPResourceDescriptor, MCPToolDescriptor


def build_local_tool_name(config: MCPServerConfig, remote_name: str) -> str:
    server_part = sanitize_name(config.tool_prefix or config.name)
    tool_part = sanitize_name(remote_name)
    return f"mcp_{server_part}__{tool_part}"


def normalize_param_schema(schema: dict | None) -> dict:
    if not isinstance(schema, dict):
        return {"type": "object", "properties": {}}
    normalized = dict(schema)
    normalized.setdefault("type", "object")
    normalized.setdefault("properties", {})
    return normalized


def should_import_tool(config: MCPServerConfig, remote_name: str, local_name: str) -> bool:
    includes = set(config.include_tools)
    excludes = set(config.exclude_tools)
    if includes and remote_name not in includes and local_name not in includes:
        return False
    if remote_name in excludes or local_name in excludes:
        return False
    return True


def build_tool_descriptor(
    config: MCPServerConfig,
    tool: Any,
    *,
    local_name: str,
) -> MCPToolDescriptor | None:
    remote_name = getattr(tool, "name", None)
    if not remote_name:
        return None

    description = (getattr(tool, "description", "") or "").strip()
    if description:
        description = f"[MCP:{config.name}] {description}"
    else:
        description = f"[MCP:{config.name}] 远端工具 {remote_name}"

    return MCPToolDescriptor(
        local_name=local_name,
        remote_name=remote_name,
        server_name=config.name,
        description=description,
        param_schema=normalize_param_schema(getattr(tool, "inputSchema", None) or getattr(tool, "input_schema", None)),
        allowed_agents=list(config.allowed_agents),
        risk_level=config.risk_level,
        require_hitl=config.require_hitl,
        timeout_sec=config.timeout_sec,
        rate_limit_per_min=config.rate_limit_per_min,
    )


def resource_descriptor_from_sdk(server_name: str, item: Any) -> MCPResourceDescriptor:
    data = dump_sdk_object(item)
    return MCPResourceDescriptor(
        name=str(data.get("name") or data.get("uri") or ""),
        uri=str(data.get("uri") or ""),
        server_name=server_name,
        description=str(data.get("description") or ""),
        mime_type=str(data.get("mimeType") or data.get("mime_type") or ""),
    )


def prompt_descriptor_from_sdk(server_name: str, item: Any) -> MCPPromptDescriptor:
    data = dump_sdk_object(item)
    args = data.get("arguments") or []
    if not isinstance(args, list):
        args = []
    return MCPPromptDescriptor(
        name=str(data.get("name") or ""),
        server_name=server_name,
        description=str(data.get("description") or ""),
        arguments=[dump_sdk_object(arg) for arg in args],
    )
