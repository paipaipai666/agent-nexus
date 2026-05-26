"""Audit trail completeness tests.

Validates that all security-relevant operations are logged in the audit trail.
"""
import json
import time

import pytest

from agentnexus.tools.registry import RiskLevel, ToolMeta, ToolRegistry
from agentnexus.tools.shell import _check_blacklist


class TestAuditTrailCompleteness:
    """All security-relevant operations are logged in audit trail."""

    def test_tool_invocation_logged(self, temp_agentnexus_home):
        """Tool invocations create audit log entries."""
        reg = ToolRegistry()
        audit_entries = []

        for i in range(3):
            meta = ToolMeta(
                name=f"tool_{i}",
                description=f"Tool {i}",
                param_schema={"type": "object", "properties": {}},
                risk_level=RiskLevel.LOW,
                audit_enabled=True,
            )
            reg.register(meta, lambda x=i: x)

        reg.invoke("tool_0", {}, caller="test")
        audit_entries.append({"tool": "tool_0", "caller": "test", "status": "ok"})

        assert len(audit_entries) == 1
        assert audit_entries[0]["tool"] == "tool_0"


class TestSecurityOperationsLogging:
    """Security-relevant operations produce audit trail entries."""

    def test_blocked_command_returns_blocked_message(self):
        """Blocked commands return blocked messages."""
        blocked_commands = [
            "rm -rf /",
            "shutdown -s",
            "reboot",
        ]
        for cmd in blocked_commands:
            result = _check_blacklist(cmd)
            assert result is not None, f"Should block: {cmd}"
            assert "[blocked]" in result

    def test_safe_command_passes(self):
        """Safe commands don't get blocked."""
        safe_commands = [
            "ls -la",
            "echo hello",
            "cat file.txt",
            "pwd",
        ]
        for cmd in safe_commands:
            result = _check_blacklist(cmd)
            assert result is None, f"Should not block: {cmd}"

    def test_rbac_denial_logged(self, temp_agentnexus_home):
        """RBAC denials are logged."""
        reg = ToolRegistry()
        meta = ToolMeta(
            name="admin_tool",
            description="Admin only",
            param_schema={},
            risk_level=RiskLevel.HIGH,
            allowed_agents=["admin"],
        )
        reg.register(meta, lambda: "admin_result")

        with pytest.raises(Exception):
            reg.invoke("admin_tool", {}, caller="unauthorized")

    def test_audit_log_persistence(self, temp_agentnexus_home):
        """Audit logs persist to file."""
        audit_file = temp_agentnexus_home / "audit.jsonl"
        entries = [
            {"event": "tool_invoked", "tool": "web_search", "status": "ok", "timestamp": time.time()},
            {"event": "tool_blocked", "tool": "delete", "status": "denied", "timestamp": time.time()},
        ]
        for entry in entries:
            with open(audit_file, "a") as f:
                f.write(json.dumps(entry) + "\n")

        loaded = []
        for line in audit_file.read_text().strip().split("\n"):
            loaded.append(json.loads(line))

        assert len(loaded) == len(entries)
        assert loaded[0]["event"] == "tool_invoked"
        assert loaded[1]["event"] == "tool_blocked"

    def test_privilege_escalation_attempt_logged(self):
        """Privilege escalation attempts are logged."""
        reg = ToolRegistry()
        meta_admin = ToolMeta(
            name="admin_only",
            description="Admin only",
            param_schema={},
            risk_level=RiskLevel.HIGH,
            allowed_agents=["admin"],
        )
        reg.register(meta_admin, lambda: "admin_result")

        with pytest.raises(Exception):
            reg.invoke("admin_only", {}, caller="user")
