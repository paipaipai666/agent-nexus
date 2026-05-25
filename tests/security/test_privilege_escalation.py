"""Security: RBAC + subagent privilege escalation tests.

Tests that the ToolRegistry's allowed_agents mechanism and the subagent
tool filtering together prevent privilege escalation through subagents.
"""

import pytest

from agentnexus.tools.registry import RiskLevel, ToolMeta, ToolRegistry


class TestSubagentRBAC:
    """Subagent callers are correctly restricted by allowed_agents."""

    def setup_method(self):
        self.registry = ToolRegistry()
        self.registry.register(
            ToolMeta(
                name="admin_tool",
                description="admin-only tool",
                param_schema={},
                allowed_agents=["admin"],
                risk_level=RiskLevel.HIGH,
            ),
            lambda: "admin_result",
        )
        self.registry.register(
            ToolMeta(
                name="medium_tool",
                description="medium-risk tool",
                param_schema={},
                allowed_agents=["react_agent", "subagent_explorer", "subagent_executor"],
                risk_level=RiskLevel.MEDIUM,
            ),
            lambda: "medium_result",
        )
        self.registry.register(
            ToolMeta(
                name="wildcard_tool",
                description="anyone can call",
                param_schema={},
                allowed_agents=["*"],
                risk_level=RiskLevel.LOW,
            ),
            lambda: "wildcard_result",
        )

    def test_subagent_cannot_call_admin_tool(self):
        """Parent agent react_agent (medium risk) → subagent cannot invoke admin-level tool."""
        with pytest.raises(PermissionError, match="not allowed"):
            self.registry.invoke("admin_tool", {}, caller="react_agent")

        with pytest.raises(PermissionError, match="not allowed"):
            self.registry.invoke("admin_tool", {}, caller="subagent_explorer")

        with pytest.raises(PermissionError, match="not allowed"):
            self.registry.invoke("admin_tool", {}, caller="subagent_executor")

    def test_allowed_agents_restriction_enforced(self):
        """Tool with allowed_agents=['admin'] raises PermissionError for react_agent."""
        with pytest.raises(PermissionError, match="not allowed"):
            self.registry.invoke("admin_tool", {}, caller="react_agent")

    def test_allowed_agent_admin_succeeds(self):
        """Admin caller can call admin-restricted tool."""
        result = self.registry.invoke("admin_tool", {}, caller="admin")
        assert result == "admin_result"

    def test_subagent_can_call_allowed_tool(self):
        """Subagent in allowed_agents can call the tool."""
        result = self.registry.invoke("medium_tool", {}, caller="subagent_explorer")
        assert result == "medium_result"

        result = self.registry.invoke("medium_tool", {}, caller="react_agent")
        assert result == "medium_result"

    def test_multi_level_nesting_maintains_rbac(self):
        """Deeply nested subagent callers maintain original RBAC restrictions.

        A subagent_subagent caller should still be blocked from admin tools.
        """
        with pytest.raises(PermissionError, match="not allowed"):
            self.registry.invoke("admin_tool", {}, caller="subagent_explorer")

        with pytest.raises(PermissionError, match="not allowed"):
            self.registry.invoke("admin_tool", {}, caller="subagent_subagent_nested")

        with pytest.raises(PermissionError, match="not allowed"):
            self.registry.invoke("admin_tool", {}, caller="subagent_subagent_subagent_deep")

    def test_risk_level_escalation_blocked(self):
        """Subagent attempting to invoke tool with higher risk combined with
        restricted allowed_agents is blocked."""
        self.registry.register(
            ToolMeta(
                name="high_risk_restricted",
                description="high risk, admin only",
                param_schema={},
                allowed_agents=["admin"],
                risk_level=RiskLevel.HIGH,
            ),
            lambda: "high_result",
        )

        with pytest.raises(PermissionError, match="not allowed"):
            self.registry.invoke("high_risk_restricted", {}, caller="subagent_explorer")

        admin_result = self.registry.invoke("high_risk_restricted", {}, caller="admin")
        assert admin_result == "high_result"

    def test_wildcard_tool_accessible_by_subagent(self):
        """Tool with allowed_agents=['*'] is accessible from any agent."""
        result = self.registry.invoke("wildcard_tool", {}, caller="react_agent")
        assert result == "wildcard_result"

        result = self.registry.invoke("wildcard_tool", {}, caller="subagent_explorer")
        assert result == "wildcard_result"

        result = self.registry.invoke("wildcard_tool", {}, caller="admin")
        assert result == "wildcard_result"

        result = self.registry.invoke("wildcard_tool", {}, caller="any_random_caller")
        assert result == "wildcard_result"

    def test_subagent_cannot_escalate_via_direct_invoke(self):
        """Subagent cannot escalate privileges by directly calling invoke
        with a different caller identity (no mechanism in ToolRegistry to do so
        — caller is always passed explicitly)."""
        self.registry.register(
            ToolMeta(
                name="parent_only_tool",
                description="only react_agent allowed",
                param_schema={},
                allowed_agents=["react_agent"],
            ),
            lambda: "parent_result",
        )

        result = self.registry.invoke("parent_only_tool", {}, caller="react_agent")
        assert result == "parent_result"

        with pytest.raises(PermissionError, match="not allowed"):
            self.registry.invoke("parent_only_tool", {}, caller="subagent_analyst")

    def test_empty_allowed_agents_fails_all_callers(self):
        """Tool with empty allowed_agents blocks all callers except wildcard match."""
        self.registry.register(
            ToolMeta(
                name="empty_allowed_tool",
                description="nobody allowed",
                param_schema={},
                allowed_agents=[],
            ),
            lambda: "never_called",
        )

        with pytest.raises(PermissionError, match="not allowed"):
            self.registry.invoke("empty_allowed_tool", {}, caller="admin")

        with pytest.raises(PermissionError, match="not allowed"):
            self.registry.invoke("empty_allowed_tool", {}, caller="react_agent")


class TestSubagentToolFiltering:
    """Subagent tool filtering ensures subagents only get safe tools."""

    def test_subagent_safe_tools_list_defined(self):
        """_SAFE_SUBAGENT_TOOLS contains only low/medium-risk tools."""
        from agentnexus.tools.subagent import _SAFE_SUBAGENT_TOOLS

        assert "python_execute" in _SAFE_SUBAGENT_TOOLS
        assert "grep_search" in _SAFE_SUBAGENT_TOOLS
        assert "file_read" in _SAFE_SUBAGENT_TOOLS
        assert len(_SAFE_SUBAGENT_TOOLS) >= 7

    def test_subagent_preset_tools_filtered_by_safe_list(self):
        """Role presets only include tools from _SAFE_SUBAGENT_TOOLS."""
        from agentnexus.tools.subagent import _ROLE_TOOL_PRESETS, _SAFE_SUBAGENT_TOOLS

        for role, tools in _ROLE_TOOL_PRESETS.items():
            for tool in tools:
                assert tool in _SAFE_SUBAGENT_TOOLS, (
                    f"Role '{role}' includes unsafe tool '{tool}'"
                )

    def test_resolve_allowed_tools_rejects_unsafe(self):
        """_resolve_allowed_tools filters out tools not in _SAFE_SUBAGENT_TOOLS."""
        from agentnexus.tools.subagent import _resolve_allowed_tools

        tools, _ = _resolve_allowed_tools("explorer", ["admin_tool", "subagent_run", "file_read"])
        assert "admin_tool" not in tools
        assert "subagent_run" not in tools
        assert "file_read" in tools
