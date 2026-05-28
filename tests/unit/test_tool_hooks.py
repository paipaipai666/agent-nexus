"""Unit tests for tool execution hooks."""

from unittest.mock import MagicMock

import pytest

from agentnexus.core.hooks import HookType, _reset_hook_manager, get_hook_manager


@pytest.fixture(autouse=True)
def clean_hooks():
    _reset_hook_manager()
    yield
    _reset_hook_manager()


class TestToolHooks:
    def _make_executor(self):
        executor = MagicMock()
        executor.registry.invoke.return_value = "tool_result"
        return executor

    def test_before_tool_call_fires(self):
        from agentnexus.agents.tool_runner import execute_tool

        mgr = get_hook_manager()
        fired = []
        mgr.register(
            HookType.BEFORE_TOOL_CALL, lambda ctx: fired.append(ctx.payload)
        )
        executor = self._make_executor()
        execute_tool(
            tool_executor=executor,
            name="test_tool",
            arguments={"k": "v"},
            caller="agent",
            hitl_approver=lambda s: True,
        )
        assert len(fired) == 1
        assert fired[0]["name"] == "test_tool"
        assert fired[0]["params"] == {"k": "v"}

    def test_before_tool_call_can_modify_params(self):
        from agentnexus.agents.tool_runner import execute_tool

        mgr = get_hook_manager()

        def modify(ctx):
            ctx.payload["params"]["timeout"] = 999

        mgr.register(HookType.BEFORE_TOOL_CALL, modify)
        executor = self._make_executor()
        execute_tool(
            tool_executor=executor,
            name="t",
            arguments={"timeout": 30},
            caller="a",
            hitl_approver=lambda s: True,
        )
        executor.registry.invoke.assert_called_once()
        call_kwargs = executor.registry.invoke.call_args
        assert call_kwargs.kwargs["params"]["timeout"] == 999

    def test_before_tool_call_abort_blocks_execution(self):
        from agentnexus.agents.tool_runner import execute_tool

        mgr = get_hook_manager()
        mgr.register(HookType.BEFORE_TOOL_CALL, lambda ctx: ctx.abort("blocked"))
        executor = self._make_executor()
        result = execute_tool(
            tool_executor=executor,
            name="t",
            arguments={},
            caller="a",
            hitl_approver=lambda s: True,
        )
        executor.registry.invoke.assert_not_called()
        assert "blocked" in result

    def test_after_tool_call_fires(self):
        from agentnexus.agents.tool_runner import execute_tool

        mgr = get_hook_manager()
        fired = []
        mgr.register(
            HookType.AFTER_TOOL_CALL, lambda ctx: fired.append(ctx.payload)
        )
        executor = self._make_executor()
        execute_tool(
            tool_executor=executor,
            name="t",
            arguments={},
            caller="a",
            hitl_approver=lambda s: True,
        )
        assert len(fired) == 1
        assert fired[0]["result"] == "tool_result"

    def test_on_tool_error_fires_on_exception(self):
        from agentnexus.agents.tool_runner import execute_tool

        mgr = get_hook_manager()
        fired = []
        mgr.register(
            HookType.ON_TOOL_ERROR, lambda ctx: fired.append(ctx.payload)
        )
        executor = self._make_executor()
        executor.registry.invoke.side_effect = RuntimeError("boom")
        result = execute_tool(
            tool_executor=executor,
            name="t",
            arguments={},
            caller="a",
            hitl_approver=lambda s: True,
        )
        assert len(fired) == 1
        assert "boom" in str(fired[0]["error"])
        assert "错误" in result

    def test_no_hooks_works_normally(self):
        from agentnexus.agents.tool_runner import execute_tool

        executor = self._make_executor()
        result = execute_tool(
            tool_executor=executor,
            name="t",
            arguments={},
            caller="a",
            hitl_approver=lambda s: True,
        )
        assert result == "tool_result"
