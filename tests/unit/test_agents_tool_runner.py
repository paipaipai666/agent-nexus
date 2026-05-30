from unittest.mock import MagicMock, patch

from agentnexus.core.hooks import HookContext, HookType


class TestExecuteTool:
    def _make_executor(self, return_value="tool_result"):
        executor = MagicMock()
        executor.registry.invoke.return_value = return_value
        return executor

    def _make_hook_ctx(self, *, aborted=False, payload=None):
        ctx = MagicMock(spec=HookContext)
        ctx.aborted = aborted
        ctx.payload = payload or {}
        ctx.abort_code = "BLOCKED"
        ctx.abort_reason = "blocked by policy"
        return ctx

    @patch("agentnexus.agents.tool_runner.get_hook_manager")
    def test_normal_execution_returns_string(self, mock_get_hook):
        from agentnexus.agents.tool_runner import execute_tool

        hook_ctx = self._make_hook_ctx(payload={"name": "t", "params": {"k": "v"}})
        mock_get_hook.return_value.fire.return_value = hook_ctx
        executor = self._make_executor("result_text")

        result = execute_tool(
            tool_executor=executor,
            name="t",
            arguments={"k": "v"},
            caller="agent",
            hitl_approver=lambda s: True,
        )
        assert result == "result_text"

    @patch("agentnexus.agents.tool_runner.get_hook_manager")
    def test_dict_result_returned_as_is(self, mock_get_hook):
        from agentnexus.agents.tool_runner import execute_tool

        hook_ctx = self._make_hook_ctx(payload={"name": "t", "params": {}})
        mock_get_hook.return_value.fire.return_value = hook_ctx
        executor = self._make_executor({"status": "ok", "data": 42})

        result = execute_tool(
            tool_executor=executor,
            name="t",
            arguments={},
            caller="agent",
            hitl_approver=lambda s: True,
        )
        assert result == {"status": "ok", "data": 42}

    @patch("agentnexus.agents.tool_runner.get_hook_manager")
    def test_hook_abort_returns_formatted_error(self, mock_get_hook):
        from agentnexus.agents.tool_runner import execute_tool

        hook_ctx = self._make_hook_ctx(aborted=True)
        hook_ctx.abort_code = "PERMISSION_DENIED"
        hook_ctx.abort_reason = "not allowed"
        mock_get_hook.return_value.fire.return_value = hook_ctx
        executor = self._make_executor()

        result = execute_tool(
            tool_executor=executor,
            name="t",
            arguments={},
            caller="agent",
            hitl_approver=lambda s: True,
        )
        executor.registry.invoke.assert_not_called()
        assert "[PERMISSION_DENIED]" in result
        assert "not allowed" in result

    @patch("agentnexus.agents.tool_runner.get_hook_manager")
    def test_cancel_checker_raises_runtime_error(self, mock_get_hook):
        from agentnexus.agents.tool_runner import execute_tool

        hook_ctx = self._make_hook_ctx(payload={"name": "t", "params": {}})
        mock_get_hook.return_value.fire.return_value = hook_ctx
        executor = self._make_executor()

        result = execute_tool(
            tool_executor=executor,
            name="t",
            arguments={},
            caller="agent",
            hitl_approver=lambda s: True,
            cancel_checker=lambda: True,
        )
        assert "错误" in result
        assert "t" in result

    @patch("agentnexus.agents.tool_runner.get_hook_manager")
    def test_exception_returns_error_string_with_tool_name(self, mock_get_hook):
        from agentnexus.agents.tool_runner import execute_tool

        hook_ctx = self._make_hook_ctx(payload={"name": "my_tool", "params": {}})
        mock_get_hook.return_value.fire.return_value = hook_ctx
        executor = self._make_executor()
        executor.registry.invoke.side_effect = ValueError("bad input")

        result = execute_tool(
            tool_executor=executor,
            name="my_tool",
            arguments={},
            caller="agent",
            hitl_approver=lambda s: True,
        )
        assert "错误" in result
        assert "my_tool" in result
        assert "bad input" in result

    @patch("agentnexus.agents.tool_runner.get_hook_manager")
    def test_hook_can_modify_params(self, mock_get_hook):
        from agentnexus.agents.tool_runner import execute_tool

        hook_ctx = self._make_hook_ctx(payload={"params": {"timeout": 999}})
        mock_get_hook.return_value.fire.return_value = hook_ctx
        executor = self._make_executor()

        execute_tool(
            tool_executor=executor,
            name="t",
            arguments={"timeout": 30},
            caller="agent",
            hitl_approver=lambda s: True,
        )
        call_kwargs = executor.registry.invoke.call_args.kwargs
        assert call_kwargs["params"]["timeout"] == 999

    @patch("agentnexus.agents.tool_runner.get_hook_manager")
    def test_after_tool_call_hook_fired_on_success(self, mock_get_hook):
        from agentnexus.agents.tool_runner import execute_tool

        hook_ctx = self._make_hook_ctx(payload={"name": "t", "params": {}})
        mock_mgr = mock_get_hook.return_value
        mock_mgr.fire.return_value = hook_ctx
        executor = self._make_executor()

        execute_tool(
            tool_executor=executor,
            name="t",
            arguments={},
            caller="agent",
            hitl_approver=lambda s: True,
        )
        calls = mock_mgr.fire.call_args_list
        assert any(c.args[0] == HookType.AFTER_TOOL_CALL for c in calls)

    @patch("agentnexus.agents.tool_runner.get_hook_manager")
    def test_on_tool_error_hook_fired_on_exception(self, mock_get_hook):
        from agentnexus.agents.tool_runner import execute_tool

        hook_ctx = self._make_hook_ctx(payload={"name": "t", "params": {}})
        mock_mgr = mock_get_hook.return_value
        mock_mgr.fire.return_value = hook_ctx
        executor = self._make_executor()
        executor.registry.invoke.side_effect = RuntimeError("boom")

        execute_tool(
            tool_executor=executor,
            name="t",
            arguments={},
            caller="agent",
            hitl_approver=lambda s: True,
        )
        calls = mock_mgr.fire.call_args_list
        assert any(c.args[0] == HookType.ON_TOOL_ERROR for c in calls)
