"""Tests for ToolRegistry"""
from agentnexus.tools.registry import ToolRegistry


class TestToolRegistry:
    def test_register_and_get(self):
        te = ToolRegistry()

        def dummy(x):
            return x

        te.register_tool("test", "desc", dummy)
        assert te.get_tool("test") is dummy

    def test_get_nonexistent(self):
        te = ToolRegistry()
        assert te.get_tool("missing") is None

    def test_get_available_tools(self):
        te = ToolRegistry()
        te.register_tool("a", "first tool", lambda: 1)
        te.register_tool("b", "second tool", lambda: 2)
        desc = te.get_available_tools()
        assert "first tool" in desc
        assert "second tool" in desc

    def test_register_overwrite(self):
        te = ToolRegistry()

        def f1(x):
            return 1

        def f2(x):
            return 2

        te.register_tool("x", "desc1", f1)
        te.register_tool("x", "desc2", f2)
        assert te.get_tool("x") is f2

    def test_hitl_blocks_when_approver_missing(self):
        te = ToolRegistry()
        te.register_tool(
            "danger",
            "desc",
            lambda code: "ok",
            param_schema={"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]},
            risk_level="high",
            require_hitl=True,
        )
        result = te.invoke(
            "danger",
            {"code": "print(1)"},
            caller="subagent_executor",
            hitl_approver=None,
        )
        assert result == "[blocked] 该工具需要人工确认，但当前没有可用的确认通道"

    def test_hitl_summary_includes_caller_tool_and_risk(self):
        te = ToolRegistry()
        seen = {}

        def approver(summary: str) -> bool:
            seen["summary"] = summary
            return False

        te.register_tool(
            "danger",
            "desc",
            lambda code: "ok",
            param_schema={"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]},
            risk_level="high",
            require_hitl=True,
        )
        result = te.invoke(
            "danger",
            {"code": "print(1)"},
            caller="subagent_executor",
            hitl_approver=approver,
        )
        assert result == "[blocked] 用户取消了该工具调用"
        assert "调用者: subagent_executor" in seen["summary"]
        assert "工具: danger" in seen["summary"]
        assert "风险: high" in seen["summary"]
