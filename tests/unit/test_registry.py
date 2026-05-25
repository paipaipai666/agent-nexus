"""Tests for agentnexus.tools.registry."""


import pytest

from agentnexus.skills.workflow import ToolPolicy
from agentnexus.tools.registry import (
    RiskLevel,
    ToolMeta,
    ToolRegistry,
)


def _make_meta(name="test_tool", **overrides):
    defaults = dict(
        name=name,
        description="A test tool",
        param_schema={"type": "object", "properties": {"x": {"type": "integer"}}},
        risk_level=RiskLevel.LOW,
        require_hitl=False,
        timeout_sec=30,
        rate_limit_per_min=0,
        audit_enabled=True,
    )
    defaults.update(overrides)
    return ToolMeta(**defaults)


class TestToolMeta:
    def test_defaults(self):
        meta = ToolMeta(name="t", description="d", param_schema={})
        assert meta.risk_level == RiskLevel.LOW
        assert meta.require_hitl is False
        assert meta.audit_enabled is True


class TestRegister:
    def test_register_new_tool(self):
        r = ToolRegistry()
        meta = _make_meta()
        r.register(meta, lambda x: x)
        assert r.get_tool("test_tool") is not None

    def test_register_overwrite_warns(self, caplog):
        r = ToolRegistry()
        meta = _make_meta()
        r.register(meta, lambda x: x)
        r.register(meta, lambda x: x)
        assert "already registered" in caplog.text


class TestInvoke:
    def test_tool_not_found(self):
        r = ToolRegistry()
        with pytest.raises(KeyError, match="not found"):
            r.invoke("nonexistent", {})

    def test_rbac_blocked(self):
        r = ToolRegistry()
        meta = _make_meta(allowed_agents=["admin"])
        r.register(meta, lambda: "ok")
        with pytest.raises(PermissionError, match="not allowed"):
            r.invoke("test_tool", {}, caller="user")

    def test_rbac_allowed(self):
        r = ToolRegistry()
        meta = _make_meta(allowed_agents=["user"])
        r.register(meta, lambda: "ok")
        result = r.invoke("test_tool", {}, caller="user")
        assert result == "ok"

    def test_hitl_required_no_approver(self):
        r = ToolRegistry()
        meta = _make_meta(require_hitl=True)
        r.register(meta, lambda: "ok")
        result = r.invoke("test_tool", {}, caller="user")
        assert "blocked" in result

    def test_hitl_rejected(self):
        r = ToolRegistry()
        meta = _make_meta(require_hitl=True)
        r.register(meta, lambda: "ok")
        result = r.invoke("test_tool", {}, caller="user", hitl_approver=lambda s: False)
        assert "取消了" in result

    def test_hitl_accepted(self):
        r = ToolRegistry()
        meta = _make_meta(require_hitl=True)
        r.register(meta, lambda: "ok")
        result = r.invoke("test_tool", {}, caller="user", hitl_approver=lambda s: True)
        assert result == "ok"

    def test_successful_execution(self):
        r = ToolRegistry()
        meta = _make_meta()
        r.register(meta, lambda x: x * 2)
        result = r.invoke("test_tool", {"x": 5})
        assert result == 10

    def test_func_error_is_raised(self):
        r = ToolRegistry()
        meta = _make_meta()
        r.register(meta, lambda: (_ for _ in ()).throw(ValueError("oops")))
        with pytest.raises(ValueError, match="oops"):
            r.invoke("test_tool", {})

    def test_timeout_raises(self):
        import time
        r = ToolRegistry()
        meta = _make_meta(timeout_sec=1)
        r.register(meta, lambda: time.sleep(10))
        with pytest.raises(TimeoutError):
            r.invoke("test_tool", {})

    def test_rate_limit_exceeded(self):
        r = ToolRegistry()
        meta = _make_meta(rate_limit_per_min=1)
        r.register(meta, lambda: "ok")
        r.invoke("test_tool", {})
        with pytest.raises(RuntimeError, match="Rate limit"):
            r.invoke("test_tool", {})

    def test_audit_log_populated(self):
        r = ToolRegistry()
        meta = _make_meta()
        r.register(meta, lambda: "done")
        r.invoke("test_tool", {})
        assert len(r._audit_log) == 1
        entry = r._audit_log[0]
        assert entry.tool_name == "test_tool"
        assert entry.duration_ms > 0


class TestQueryAPI:
    def test_get_available_tools(self):
        r = ToolRegistry()
        meta = _make_meta(description="my tool")
        r.register(meta, lambda: None)
        text = r.get_available_tools("user")
        assert "my tool" in text

    def test_get_available_tools_empty(self):
        r = ToolRegistry()
        assert r.get_available_tools("user") == "(no tools available)"

    def test_get_tool_found(self):
        r = ToolRegistry()
        def fn():
            return 42
        r.register(_make_meta(), fn)
        assert r.get_tool("test_tool") is fn

    def test_get_tool_not_found(self):
        r = ToolRegistry()
        assert r.get_tool("nonexistent") is None

    def test_list_tools(self):
        r = ToolRegistry()
        r.register(_make_meta(name="a"), lambda: None)
        r.register(_make_meta(name="b"), lambda: None)
        assert sorted(r.list_tools()) == ["a", "b"]

    def test_to_openai_tools(self):
        r = ToolRegistry()
        meta = _make_meta(
            name="search",
            description="Search tool",
            param_schema={
                "type": "object",
                "properties": {"q": {"type": "string", "default": ""}},
                "required": [],
            },
        )
        r.register(meta, lambda: None)
        tools = r.to_openai_tools("user")
        assert len(tools) == 1
        assert tools[0]["function"]["name"] == "search"
        assert tools[0]["function"]["parameters"]["type"] == "object"

    def test_to_openai_tools_no_properties(self):
        r = ToolRegistry()
        meta = _make_meta(name="noargs", param_schema={"type": "object", "properties": {}})
        r.register(meta, lambda: None)
        tools = r.to_openai_tools("user")
        assert "required" not in tools[0]["function"]["parameters"]

    def test_to_openai_tools_respects_agent_filter(self):
        r = ToolRegistry()
        r.register(_make_meta(name="admin_tool", allowed_agents=["admin"]), lambda: None)
        r.register(_make_meta(name="user_tool", allowed_agents=["*"]), lambda: None)
        tools = r.to_openai_tools("user")
        names = [t["function"]["name"] for t in tools]
        assert "user_tool" in names
        assert "admin_tool" not in names

    def test_get_available_tools_respects_tool_policy(self):
        r = ToolRegistry()
        r.register(_make_meta(name="file_read", description="read", risk_level=RiskLevel.LOW), lambda: None)
        r.register(_make_meta(name="shell_exec", description="shell", risk_level=RiskLevel.HIGH), lambda: None)
        policy = ToolPolicy(allow=["file_read", "shell_exec"], max_risk="low", allow_subagents=False)
        text = r.get_available_tools("user", tool_policy=policy)
        assert "file_read" in text
        assert "shell_exec" not in text

    def test_invoke_respects_tool_policy_and_audits_block(self):
        r = ToolRegistry()
        called = {"value": False}

        def shell():
            called["value"] = True
            return "ok"

        r.register(_make_meta(name="shell_exec", risk_level=RiskLevel.HIGH), shell)
        policy = ToolPolicy(allow=["shell_exec"], max_risk="low")

        with pytest.raises(PermissionError, match="not visible"):
            r.invoke("shell_exec", {}, caller="react_agent", tool_policy=policy)

        assert called["value"] is False
        audit = r.get_audit_log()
        assert len(audit) == 1
        assert audit[0].tool_name == "shell_exec"
        assert "not visible" in audit[0].error

    def test_invoke_without_tool_policy_preserves_existing_behavior(self):
        r = ToolRegistry()
        r.register(_make_meta(name="shell_exec", risk_level=RiskLevel.HIGH), lambda: "ok")
        assert r.invoke("shell_exec", {}, caller="react_agent") == "ok"

    def test_to_openai_tools_respects_tool_policy(self):
        r = ToolRegistry()
        r.register(_make_meta(name="file_read", risk_level=RiskLevel.LOW), lambda: None)
        r.register(_make_meta(name="web_search", risk_level=RiskLevel.LOW), lambda: None)
        policy = ToolPolicy(allow=["file_read", "web_search"], deny=["web_search"], max_risk="low")
        tools = r.to_openai_tools("user", tool_policy=policy)
        names = [t["function"]["name"] for t in tools]
        assert names == ["file_read"]

    def test_get_audit_log_returns_copy(self):
        r = ToolRegistry()
        log = r.get_audit_log()
        assert log == []


class TestValidation:
    def test_validate_params_skipped_when_jsonschema_missing(self):
        r = ToolRegistry()
        meta = _make_meta(param_schema={"type": "object", "properties": {"x": {"type": "integer"}}})
        r.register(meta, lambda x: x)
        result = r.invoke("test_tool", {"x": 5})
        assert result == 5

    def test_validate_params_fails(self):
        r = ToolRegistry()
        meta = _make_meta(param_schema={
            "type": "object", "properties": {"x": {"type": "integer"}}, "required": ["x"]
        })
        r.register(meta, lambda x: x)
        with pytest.raises(ValueError, match="parameter validation failed"):
            r.invoke("test_tool", {"x": "not_an_int"})

    def test_output_schema_invalid(self, caplog):
        r = ToolRegistry()
        meta = _make_meta(output_schema={
            "type": "object", "properties": {"key": {"type": "string"}}
        })
        r.register(meta, lambda: {"key": 123})
        result = r.invoke("test_tool", {})
        assert result == {"key": 123}
