from unittest.mock import MagicMock, patch

from agentnexus.core.hooks import HookContext, HookType
from agentnexus.tools.errors import ToolError


class TestExecuteTool:
    def _make_executor(self, return_value="tool_result"):
        executor = MagicMock()
        executor.invoke.return_value = return_value
        return executor

    def _make_hook_ctx(self, *, aborted=False, payload=None):
        ctx = MagicMock(spec=HookContext)
        ctx.aborted = aborted
        ctx.payload = payload or {}
        ctx.abort_code = "BLOCKED"
        ctx.abort_reason = "blocked by policy"
        ctx.to_feedback.side_effect = (
            lambda: f"[hook blocked] {ctx.abort_code}: {ctx.abort_reason}"
        )
        return ctx

    @patch("agentnexus.core.hooks.get_hook_manager")
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

    @patch("agentnexus.core.hooks.get_hook_manager")
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

    @patch("agentnexus.core.hooks.get_hook_manager")
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
        executor.invoke.assert_not_called()
        assert isinstance(result, ToolError)
        assert result.error_code == "PERMISSION_DENIED"
        assert "not allowed" in result.message

    @patch("agentnexus.core.hooks.get_hook_manager")
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
        assert isinstance(result, ToolError)
        assert result.error_code == "CANCELLED"
        assert "t" in result.message

    @patch("agentnexus.core.hooks.get_hook_manager")
    def test_exception_returns_error_string_with_tool_name(self, mock_get_hook):
        from agentnexus.agents.tool_runner import execute_tool

        hook_ctx = self._make_hook_ctx(payload={"name": "my_tool", "params": {}})
        mock_get_hook.return_value.fire.return_value = hook_ctx
        executor = self._make_executor()
        executor.invoke.side_effect = ValueError("bad input")

        result = execute_tool(
            tool_executor=executor,
            name="my_tool",
            arguments={},
            caller="agent",
            hitl_approver=lambda s: True,
        )
        assert isinstance(result, ToolError)
        assert result.error_code == "VALIDATION_FAILED"
        # LOW-02: ValueError is a safe domain exception, message preserved
        assert "bad input" in result.message

    @patch("agentnexus.core.hooks.get_hook_manager")
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
        call_kwargs = executor.invoke.call_args.kwargs
        assert call_kwargs["params"]["timeout"] == 999

    @patch("agentnexus.core.hooks.get_hook_manager")
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

    @patch("agentnexus.core.hooks.get_hook_manager")
    def test_on_tool_error_hook_fired_on_exception(self, mock_get_hook):
        from agentnexus.agents.tool_runner import execute_tool

        hook_ctx = self._make_hook_ctx(payload={"name": "t", "params": {}})
        mock_mgr = mock_get_hook.return_value
        mock_mgr.fire.return_value = hook_ctx
        executor = self._make_executor()
        executor.invoke.side_effect = RuntimeError("boom")

        execute_tool(
            tool_executor=executor,
            name="t",
            arguments={},
            caller="agent",
            hitl_approver=lambda s: True,
        )
        calls = mock_mgr.fire.call_args_list
        assert any(c.args[0] == HookType.ON_TOOL_ERROR for c in calls)


def _make_hook_ctx(*, aborted=False):
    ctx = MagicMock(spec=HookContext)
    ctx.aborted = aborted
    ctx.payload = {}
    return ctx


class TestHitlAcrossThreadHop:
    """execute_tool hops to a pool worker thread; thread-affine approvers
    (ConfirmBridge) must still route to the target registered by the
    submitting thread — regression for shell_exec always returning
    '[blocked] 用户取消了该工具调用'."""

    @patch("agentnexus.core.hooks.get_hook_manager")
    def test_confirm_bridge_target_reached_across_pool_hop(self, mock_get_hook):
        import threading

        from agentnexus.agents.tool_runner import execute_tool
        from agentnexus.tools.confirm_bridge import ConfirmBridge
        from agentnexus.tools.registry import ToolRegistry, ToolMeta, RiskLevel

        hook_ctx = _make_hook_ctx()
        mock_get_hook.return_value.fire.return_value = hook_ctx

        registry = ToolRegistry()
        registry.register(
            ToolMeta(
                name="shell_exec",
                description="fake shell",
                param_schema={
                    "type": "object",
                    "properties": {"command": {"type": "string"}},
                    "required": ["command"],
                },
                risk_level=RiskLevel.HIGH,
                require_hitl=True,
            ),
            lambda command: f"executed: {command}",
        )
        bridge = ConfirmBridge()
        seen = []

        def ws_confirm(summary):
            seen.append(summary)
            return True

        def run_agent():
            tid = threading.get_ident()
            bridge.set_target(ws_confirm, thread_id=tid)
            try:
                return execute_tool(
                    tool_executor=registry,
                    name="shell_exec",
                    arguments={"command": "echo hi"},
                    caller="react_agent",
                    hitl_approver=bridge,
                )
            finally:
                bridge.set_target(None, thread_id=tid)

        thread = threading.Thread(target=lambda: results.append(run_agent()))
        results = []
        thread.start()
        thread.join(timeout=30)

        assert not thread.is_alive()
        assert results == ["executed: echo hi"]
        assert len(seen) == 1

    @patch("agentnexus.core.hooks.get_hook_manager")
    def test_confirm_bridge_no_target_still_fails_closed(self, mock_get_hook):

        from agentnexus.agents.tool_runner import execute_tool
        from agentnexus.tools.confirm_bridge import ConfirmBridge
        from agentnexus.tools.registry import ToolRegistry, ToolMeta, RiskLevel

        hook_ctx = _make_hook_ctx()
        mock_get_hook.return_value.fire.return_value = hook_ctx

        registry = ToolRegistry()
        registry.register(
            ToolMeta(
                name="shell_exec",
                description="fake shell",
                param_schema={"type": "object", "properties": {"command": {"type": "string"}},
                              "required": ["command"]},
                risk_level=RiskLevel.HIGH,
                require_hitl=True,
            ),
            lambda command: "should not run",
        )

        result = execute_tool(
            tool_executor=registry,
            name="shell_exec",
            arguments={"command": "echo hi"},
            caller="react_agent",
            hitl_approver=ConfirmBridge(),
        )
        assert "用户取消" in result
