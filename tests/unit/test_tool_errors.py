"""Tests for ToolError structured error returns."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from agentnexus.tools.errors import ToolError, ToolErrorCode

# ── ToolError dataclass ──────────────────────────────────────────


class TestToolErrorCode:
    """Verify all expected error codes exist."""

    def test_all_codes_defined(self):
        expected = {
            "RATE_LIMITED", "TIMEOUT", "PERMISSION_DENIED",
            "VALIDATION_FAILED", "CANCELLED", "EXECUTION_FAILED",
            "HITL_BLOCKED", "TOOL_NOT_FOUND", "SCHEMA_VALIDATION_FAILED",
        }
        actual = {member.value for member in ToolErrorCode}
        assert expected == actual

    def test_codes_are_strings(self):
        for member in ToolErrorCode:
            assert isinstance(member.value, str)


class TestToolErrorConstruction:
    """ToolError dataclass builds correctly."""

    def test_basic_construction(self):
        err = ToolError(
            error_code=ToolErrorCode.TIMEOUT,
            message="Tool timed out",
            recoverable=True,
            suggested_action="Retry with a longer timeout",
        )
        assert err.error_code == ToolErrorCode.TIMEOUT
        assert err.message == "Tool timed out"
        assert err.recoverable is True
        assert err.suggested_action == "Retry with a longer timeout"

    def test_str_renders_formatted_string(self):
        err = ToolError(
            error_code=ToolErrorCode.PERMISSION_DENIED,
            message="Agent not allowed",
            recoverable=False,
            suggested_action="Check agent permissions",
        )
        result = str(err)
        assert "PERMISSION_DENIED" in result
        assert "Agent not allowed" in result
        assert "Check agent permissions" in result

    def test_str_is_valid_for_llm_tool_messages(self):
        """LLM tool message content must be a plain string."""
        err = ToolError(
            error_code=ToolErrorCode.TIMEOUT,
            message="timed out after 60s",
            recoverable=True,
            suggested_action="Retry",
        )
        content = str(err)
        assert isinstance(content, str)
        assert len(content) > 0
        # Should not contain Python object repr
        assert "ToolError(" not in content


class TestToolErrorEquality:
    """ToolError instances with same fields are equal."""

    def test_equal_instances(self):
        a = ToolError("TIMEOUT", "msg", True, "retry")
        b = ToolError("TIMEOUT", "msg", True, "retry")
        assert a == b

    def test_different_code_not_equal(self):
        a = ToolError("TIMEOUT", "msg", True, "retry")
        b = ToolError("CANCELLED", "msg", True, "retry")
        assert a != b


# ── execute_tool returns ToolError on failure ────────────────────


class TestExecuteToolReturnsToolError:
    """execute_tool should return ToolError (not plain strings) on failure."""

    def _make_executor(self, return_value="tool_result"):
        executor = MagicMock()
        executor.invoke.return_value = return_value
        return executor

    def _make_hook_ctx(self, *, aborted=False, payload=None):
        ctx = MagicMock()
        ctx.aborted = aborted
        ctx.payload = payload or {}
        ctx.abort_code = "BLOCKED"
        ctx.abort_reason = "blocked by policy"
        return ctx

    @patch("agentnexus.agents.tool_runner.get_hook_manager")
    def test_hook_abort_returns_tool_error(self, mock_get_hook):
        from agentnexus.agents.tool_runner import execute_tool

        hook_ctx = self._make_hook_ctx(aborted=True)
        hook_ctx.abort_code = "PERMISSION_DENIED"
        hook_ctx.abort_reason = "not allowed"
        mock_get_hook.return_value.fire.return_value = hook_ctx
        executor = self._make_executor()

        result = execute_tool(
            tool_executor=executor, name="t", arguments={},
            caller="agent", hitl_approver=lambda s: True,
        )
        assert isinstance(result, ToolError)
        assert result.error_code == "PERMISSION_DENIED"
        assert result.recoverable is False

    @patch("agentnexus.agents.tool_runner.get_hook_manager")
    def test_cancel_returns_tool_error(self, mock_get_hook):
        from agentnexus.agents.tool_runner import execute_tool

        hook_ctx = self._make_hook_ctx(payload={"name": "t", "params": {}})
        mock_get_hook.return_value.fire.return_value = hook_ctx
        executor = self._make_executor()

        result = execute_tool(
            tool_executor=executor, name="t", arguments={},
            caller="agent", hitl_approver=lambda s: True,
            cancel_checker=lambda: True,
        )
        assert isinstance(result, ToolError)
        assert result.error_code == ToolErrorCode.CANCELLED
        assert result.recoverable is True

    @patch("agentnexus.agents.tool_runner.get_hook_manager")
    def test_permission_error_returns_tool_error(self, mock_get_hook):
        from agentnexus.agents.tool_runner import execute_tool

        hook_ctx = self._make_hook_ctx(payload={"name": "t", "params": {}})
        mock_get_hook.return_value.fire.return_value = hook_ctx
        executor = self._make_executor()
        executor.invoke.side_effect = PermissionError("denied")

        result = execute_tool(
            tool_executor=executor, name="t", arguments={},
            caller="agent", hitl_approver=lambda s: True,
        )
        assert isinstance(result, ToolError)
        assert result.error_code == ToolErrorCode.PERMISSION_DENIED
        assert result.recoverable is False

    @patch("agentnexus.agents.tool_runner.get_hook_manager")
    def test_value_error_returns_tool_error(self, mock_get_hook):
        from agentnexus.agents.tool_runner import execute_tool

        hook_ctx = self._make_hook_ctx(payload={"name": "t", "params": {}})
        mock_get_hook.return_value.fire.return_value = hook_ctx
        executor = self._make_executor()
        executor.invoke.side_effect = ValueError("bad input")

        result = execute_tool(
            tool_executor=executor, name="t", arguments={},
            caller="agent", hitl_approver=lambda s: True,
        )
        assert isinstance(result, ToolError)
        assert result.error_code == ToolErrorCode.VALIDATION_FAILED
        assert "bad input" in result.message

    @patch("agentnexus.agents.tool_runner.get_hook_manager")
    def test_generic_error_returns_tool_error(self, mock_get_hook):
        from agentnexus.agents.tool_runner import execute_tool

        hook_ctx = self._make_hook_ctx(payload={"name": "t", "params": {}})
        mock_get_hook.return_value.fire.return_value = hook_ctx
        executor = self._make_executor()
        executor.invoke.side_effect = RuntimeError("boom")

        result = execute_tool(
            tool_executor=executor, name="t", arguments={},
            caller="agent", hitl_approver=lambda s: True,
        )
        assert isinstance(result, ToolError)
        assert result.error_code == ToolErrorCode.EXECUTION_FAILED
        # Generic errors have message stripped (LOW-02)
        assert "boom" not in result.message

    @patch("agentnexus.agents.tool_runner.get_hook_manager")
    def test_success_returns_original_value(self, mock_get_hook):
        """Normal execution path unchanged — still returns str or dict."""
        from agentnexus.agents.tool_runner import execute_tool

        hook_ctx = self._make_hook_ctx(payload={"name": "t", "params": {}})
        mock_get_hook.return_value.fire.return_value = hook_ctx
        executor = self._make_executor("ok")

        result = execute_tool(
            tool_executor=executor, name="t", arguments={},
            caller="agent", hitl_approver=lambda s: True,
        )
        assert result == "ok"
        assert not isinstance(result, ToolError)


# ── summarize_tool_result handles ToolError ──────────────────────


class TestSummarizeToolResultWithToolError:
    """summarize_tool_result should render ToolError as a string."""

    def test_tool_error_rendered_as_string(self):
        from agentnexus.tools.result_format import summarize_tool_result

        err = ToolError(
            error_code=ToolErrorCode.TIMEOUT,
            message="timed out",
            recoverable=True,
            suggested_action="Retry",
        )
        result = summarize_tool_result(err)
        assert isinstance(result, str)
        assert "TIMEOUT" in result
        assert "timed out" in result
