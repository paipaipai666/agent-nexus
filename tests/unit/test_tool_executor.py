"""Tests for ToolExecutor"""
from agentnexus.tools.tool_executor import ToolExecutor


class TestToolExecutor:
    def test_register_and_get(self):
        te = ToolExecutor()

        def dummy(x):
            return x

        te.registerTool("test", "desc", dummy)
        assert te.getTool("test") is dummy

    def test_get_nonexistent(self):
        te = ToolExecutor()
        assert te.getTool("missing") is None

    def test_get_available_tools(self):
        te = ToolExecutor()
        te.registerTool("a", "first tool", lambda: 1)
        te.registerTool("b", "second tool", lambda: 2)
        desc = te.getAvailableTools()
        assert "first tool" in desc
        assert "second tool" in desc

    def test_register_overwrite(self):
        te = ToolExecutor()

        def f1(x):
            return 1

        def f2(x):
            return 2

        te.registerTool("x", "desc1", f1)
        te.registerTool("x", "desc2", f2)
        assert te.getTool("x") is f2
