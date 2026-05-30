"""MCP descriptor call dispatch helpers."""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)
from collections.abc import Awaitable, Callable
from typing import Any

from agentnexus.tools import mcp_result
from agentnexus.tools.mcp_schema import MCPToolDescriptor, ServerRuntime


async def call_descriptor(
    runtime: ServerRuntime,
    descriptor: MCPToolDescriptor,
    params: dict,
    *,
    resource_descriptors: dict[str, list],
    resource_template_descriptors: dict[str, list[dict]],
    prompt_descriptors: dict[str, list],
) -> str:
    if descriptor.capability == "tool":
        result = await asyncio.wait_for(
            runtime.session.call_tool(descriptor.remote_name, arguments=params),
            timeout=descriptor.timeout_sec,
        )
        text = mcp_result.normalize_tool_result(result)
        if getattr(result, "isError", False) or getattr(result, "is_error", False):
            raise RuntimeError(text or f"MCP tool '{descriptor.remote_name}' returned an error")
        return text
    if descriptor.remote_name == "list_resources":
        resources = resource_descriptors.get(descriptor.server_name, [])
        return mcp_result.json_text([item.__dict__ for item in resources])
    if descriptor.remote_name == "list_resource_templates":
        return mcp_result.json_text(resource_template_descriptors.get(descriptor.server_name, []))
    if descriptor.remote_name == "read_resource":
        result = await asyncio.wait_for(runtime.session.read_resource(params["uri"]), timeout=descriptor.timeout_sec)
        return mcp_result.normalize_resource_result(result)
    if descriptor.remote_name == "list_prompts":
        prompts = prompt_descriptors.get(descriptor.server_name, [])
        return mcp_result.json_text([item.__dict__ for item in prompts])
    if descriptor.remote_name == "get_prompt":
        result = await asyncio.wait_for(
            runtime.session.get_prompt(params["name"], arguments=params.get("arguments") or None),
            timeout=descriptor.timeout_sec,
        )
        return mcp_result.normalize_prompt_result(result)
    raise RuntimeError(f"Unsupported MCP capability: {descriptor.remote_name}")


async def run_with_limiter(
    runtime: ServerRuntime,
    call: Callable[[], Awaitable[str]],
) -> str:
    limiter: Any = runtime.semaphore or runtime.call_lock
    if limiter is None:
        logger.warning("ServerRuntime semaphore not initialized, creating per-call limiter")
        limiter = asyncio.Semaphore(1)
    async with limiter:
        return await call()
