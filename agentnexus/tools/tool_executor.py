"""Backward-compatible ToolExecutor wrapping the new ToolRegistry."""

from agentnexus.tools.registry import RiskLevel, ToolMeta, ToolRegistry


class ToolExecutor:
    """Thin wrapper around ToolRegistry — existing code works unchanged."""

    def __init__(self):
        self.registry = ToolRegistry()

    def registerTool(self, name: str, description: str, func: callable,
                     param_schema: dict | None = None,
                     allowed_agents: list[str] | None = None,
                     risk_level: str = "low",
                     require_hitl: bool = False,
                     timeout_sec: int = 30,
                     rate_limit_per_min: int = 0,
                     output_schema: dict | None = None,
                     audit_enabled: bool = True):
        """Register a tool with full metadata. Extra kwargs map to ToolMeta fields.

        Legacy signature (name, description, func) still works — metadata defaults apply.
        """
        risk = getattr(RiskLevel, risk_level.upper(), RiskLevel.LOW)
        meta = ToolMeta(
            name=name,
            description=description,
            param_schema=param_schema or {"type": "object", "properties": {}},
            allowed_agents=allowed_agents or ["*"],
            risk_level=risk,
            require_hitl=require_hitl,
            timeout_sec=timeout_sec,
            rate_limit_per_min=rate_limit_per_min,
            output_schema=output_schema,
            audit_enabled=audit_enabled,
        )
        self.registry.register(meta, func)

    def getTool(self, name: str):
        """Legacy API: return raw callable."""
        return self.registry.get_tool(name)

    def getAvailableTools(self, agent: str = "*", tool_policy=None) -> str:
        """Legacy API: return formatted description string."""
        return self.registry.get_available_tools(agent, tool_policy=tool_policy)
