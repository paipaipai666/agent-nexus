"""Shared fixtures and helpers for MCP adapter tests."""

from __future__ import annotations

from types import SimpleNamespace

from agentnexus.tools.mcp_adapter import MCPToolDescriptor


class FakeExitStack:
    async def aclose(self):
        return None


def _make_descriptor(**overrides) -> MCPToolDescriptor:
    defaults = dict(
        local_name="mcp_docs__search",
        remote_name="search",
        server_name="docs",
        description="[MCP:docs] 搜索文档",
        param_schema={"type": "object", "properties": {"q": {"type": "string"}}},
        allowed_agents=["react_agent"],
        risk_level="medium",
        require_hitl=False,
        timeout_sec=30,
        rate_limit_per_min=5,
    )
    defaults.update(overrides)
    return MCPToolDescriptor(**defaults)
