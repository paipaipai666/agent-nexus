"""Connection-level helpers for the MCP adapter."""

from __future__ import annotations

import inspect

import httpx

from agentnexus.core.config import MCPServerConfig


def build_http_client_kwargs(factory, config: MCPServerConfig, http_client: httpx.AsyncClient) -> dict:
    params = inspect.signature(factory).parameters
    kwargs = {}
    if "url" in params:
        kwargs["url"] = config.url
    else:
        kwargs["server_url"] = config.url
    if "http_client" in params:
        kwargs["http_client"] = http_client
    return kwargs


def ensure_sdk_available() -> None:
    try:
        import mcp  # noqa: F401
    except ImportError as exc:
        raise RuntimeError("MCP SDK is not installed; install dependency 'mcp' first") from exc
