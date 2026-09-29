"""Tool registry security tests — RBAC, rate limiting, HITL gate,
and audit logging hygiene."""

import time

import pytest

from agentnexus.tools.registry import RiskLevel, ToolMeta, ToolRegistry


class TestRegistryRBAC:
    """Tool-level RBAC — restricted callers are rejected."""

    def setup_method(self):
        self.registry = ToolRegistry()
        self.registry.register(
            ToolMeta(
                name="restricted_tool",
                description="test",
                param_schema={},
                allowed_agents=["admin"],
                risk_level=RiskLevel.HIGH,
            ),
            lambda: "done",
        )

    def test_rbac_allowed_agent_succeeds(self):
        """Agent in allowed_agents can call the tool."""
        result = self.registry.invoke("restricted_tool", {}, caller="admin")
        assert result == "done"

    def test_rbac_unauthorized_agent_raises(self):
        """Agent not in allowed_agents is rejected."""
        with pytest.raises(PermissionError, match="not allowed"):
            self.registry.invoke("restricted_tool", {}, caller="hacker")

    def test_rbac_wildcard_allows_any(self):
        """Tool with allowed_agents=['*'] allows any caller."""
        reg = ToolRegistry()
        reg.register(
            ToolMeta(name="open_tool", description="test", param_schema={},
                     allowed_agents=["*"]),
            lambda: "ok",
        )
        result = reg.invoke("open_tool", {}, caller="stranger")
        assert result == "ok"

    def test_rbac_unknown_tool_raises(self):
        """Calling non-existent tool raises KeyError."""
        with pytest.raises(KeyError, match="not found"):
            self.registry.invoke("nonexistent", {}, caller="admin")


class TestRegistryRateLimit:
    """Rate limiting — excessive calls are blocked."""

    def setup_method(self):
        self.registry = ToolRegistry()
        self.registry.register(
            ToolMeta(name="limited_tool", description="test", param_schema={},
                     rate_limit_per_min=3, risk_level=RiskLevel.LOW),
            lambda: "ok",
        )

    def test_rate_limit_allows_under(self):
        """Calls within limit succeed."""
        for _ in range(3):
            result = self.registry.invoke("limited_tool", {}, caller="test")
            assert result == "ok"

    def test_rate_limit_blocks_excess(self):
        """Call exceeding rate limit raises RuntimeError."""
        for _ in range(3):
            self.registry.invoke("limited_tool", {}, caller="test")
        with pytest.raises(RuntimeError, match="Rate limit exceeded"):
            self.registry.invoke("limited_tool", {}, caller="test")

    def test_rate_limit_window_expires(self):
        """After 60s window, rate counter resets (verify cleanup does not crash)."""
        for _ in range(3):
            self.registry.invoke("limited_tool", {}, caller="test")
        # Simulate time passing — just verify window cleanup is safe
        now = time.time()
        self.registry._rate_counters["limited_tool"] = [now - 70]
        # One more call should work since old entries were removed
        result = self.registry.invoke("limited_tool", {}, caller="test")
        assert result == "ok"


class TestRegistryHITL:
    """Human-in-the-loop gate — blocks when approver returns False."""

    def setup_method(self):
        self.registry = ToolRegistry()
        self.registry.register(
            ToolMeta(name="risky_tool", description="test", param_schema={},
                     require_hitl=True, risk_level=RiskLevel.HIGH),
            lambda: "done",
        )

    def test_hitl_approver_true_succeeds(self):
        """HITL with approver returning True allows execution."""
        result = self.registry.invoke("risky_tool", {}, caller="test",
                                      hitl_approver=lambda _: True)
        assert result == "done"

    def test_hitl_approver_false_blocks(self):
        """HITL with approver returning False blocks execution."""
        result = self.registry.invoke("risky_tool", {}, caller="test",
                                      hitl_approver=lambda _: False)
        assert "blocked" in result

    def test_hitl_no_approver_blocks(self):
        """HITL with no approver returns blocked message."""
        result = self.registry.invoke("risky_tool", {}, caller="test")
        assert "blocked" in result


class TestRegistryAudit:
    """Audit log — sensitive data must not appear raw."""

    def test_audit_log_contains_expected_fields(self):
        """AuditEntry records tool name, caller, duration."""
        reg = ToolRegistry()
        reg.register(
            ToolMeta(name="test_tool", description="test", param_schema={}),
            lambda **kw: "result_data",
        )
        reg.invoke("test_tool", {"key": "value"}, caller="agent1")
        log = reg.get_audit_log()
        assert len(log) == 1
        entry = log[0]
        assert entry.tool_name == "test_tool"
        assert entry.caller == "agent1"
        assert entry.duration_ms >= 0

    def test_audit_params_truncated(self):
        """Params longer than 300 chars are truncated."""
        reg = ToolRegistry()
        reg.register(
            ToolMeta(name="verbose_tool", description="test", param_schema={}),
            lambda **kw: "done",
        )
        long_val = "x" * 1000
        reg.invoke("verbose_tool", {"data": long_val}, caller="agent1")
        entry = reg.get_audit_log()[0]
        assert len(entry.params) <= 310

    def test_audit_params_no_api_key(self):
        """Audit params should not contain raw API key values."""
        reg = ToolRegistry()
        reg.register(
            ToolMeta(name="api_tool", description="test", param_schema={}),
            lambda **kw: "done",
        )
        reg.invoke("api_tool", {"api_key": "sk-leaked-12345"}, caller="agent1")
        entry = reg.get_audit_log()[0]
        assert "sk-leaked" not in entry.params
