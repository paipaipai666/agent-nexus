"""越界路径的 HITL 放行通道。

覆盖四个分支：用户批准（放行本次会话）、用户拒绝（HITL_BLOCKED 且不执行）、
无确认通道（保持旧硬错误语义）、config allowed_paths（永久列表）。
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agentnexus.tools.errors import ToolError, ToolErrorCode
from agentnexus.tools.file_ops import (
    PathEscapesAllowedRootsError,
    _resolve_safe,
    file_read,
    path_guard_violation,
)
from agentnexus.tools.workspace import (
    clear_approved_paths,
    current_workspace,
)


def _make_executor(return_value="ok"):
    executor = MagicMock()
    executor.invoke.return_value = return_value
    return executor


def _make_hook_ctx():
    from agentnexus.core.hooks import HookContext

    ctx = MagicMock(spec=HookContext)
    ctx.aborted = False
    ctx.payload = {}
    ctx.abort_code = ""
    ctx.abort_reason = ""
    ctx.to_feedback.return_value = ""
    return ctx


@patch("agentnexus.core.hooks.get_hook_manager")
class TestOutOfBoundsPrompt:
    def test_guard_violation_detected_before_invoke(self, mock_get_hook):
        from agentnexus.agents.tool_runner import execute_tool

        mock_get_hook.return_value.fire.return_value = _make_hook_ctx()
        outside = Path.cwd().parent / "definitely_not_inside_ws.txt"
        violation = path_guard_violation("file_read", {"path": str(outside)})
        assert isinstance(violation, PathEscapesAllowedRootsError)
        assert "路径越界" in str(violation)

        prompts: list[str] = []
        executor = _make_executor("read-ok")

        def approver(summary: str) -> bool:
            prompts.append(summary)
            return True

        result = execute_tool(
            tool_executor=executor,
            name="file_read",
            arguments={"path": str(outside)},
            caller="agent",
            hitl_approver=approver,
        )
        assert result == "read-ok"
        executor.invoke.assert_called_once()
        assert len(prompts) == 1
        assert "路径越界请求放行" in prompts[0]
        assert str(outside) in prompts[0]
        assert "allowed_paths" in prompts[0]

    def test_approval_allows_real_read_then_revocable(self, mock_get_hook, tmp_path, monkeypatch):
        from agentnexus.agents.tool_runner import execute_tool

        mock_get_hook.return_value.fire.return_value = _make_hook_ctx()
        ws = tmp_path / "ws"
        outside_dir = tmp_path / "outside"
        ws.mkdir()
        outside_dir.mkdir()
        f = outside_dir / "note.txt"
        f.write_text("secret", encoding="utf-8")
        monkeypatch.chdir(ws)
        token = current_workspace.set(str(ws))
        try:
            clear_approved_paths()
            with pytest.raises(ValueError, match="路径越界"):
                file_read(str(f))

            executor = _make_executor()
            execute_tool(
                tool_executor=executor,
                name="file_read",
                arguments={"path": str(f)},
                caller="agent",
                hitl_approver=lambda s: True,
            )
            # Approval is visible to the real guard: file_read now succeeds.
            assert "secret" in file_read(str(f))

            clear_approved_paths()
            with pytest.raises(ValueError, match="路径越界"):
                file_read(str(f))
        finally:
            current_workspace.reset(token)
            clear_approved_paths()

    def test_approval_scoped_to_workspace(self, mock_get_hook, tmp_path):
        from agentnexus.agents.tool_runner import execute_tool

        mock_get_hook.return_value.fire.return_value = _make_hook_ctx()
        ws1 = tmp_path / "ws1"
        ws2 = tmp_path / "ws2"
        outside = tmp_path / "outside"
        for d in (ws1, ws2, outside):
            d.mkdir()
        f = outside / "a.txt"
        f.write_text("x", encoding="utf-8")

        clear_approved_paths()
        t1 = current_workspace.set(str(ws1))
        try:
            execute_tool(
                tool_executor=_make_executor(),
                name="file_read",
                arguments={"path": str(f)},
                caller="agent",
                hitl_approver=lambda s: True,
            )
            assert "x" in file_read(str(f))

            # A concurrent session bound to another workspace must NOT inherit.
            t2 = current_workspace.set(str(ws2))
            try:
                with pytest.raises(ValueError, match="路径越界"):
                    file_read(str(f))
            finally:
                current_workspace.reset(t2)
        finally:
            current_workspace.reset(t1)
            clear_approved_paths()

    def test_deny_returns_hitl_blocked_and_skips_invoke(self, mock_get_hook, tmp_path, monkeypatch):
        from agentnexus.agents.tool_runner import execute_tool

        mock_get_hook.return_value.fire.return_value = _make_hook_ctx()
        ws = tmp_path / "ws"
        outside = tmp_path / "outside" / "x.txt"
        ws.mkdir()
        outside.parent.mkdir()
        monkeypatch.chdir(ws)
        token = current_workspace.set(str(ws))
        try:
            clear_approved_paths()
            executor = _make_executor()
            result = execute_tool(
                tool_executor=executor,
                name="file_read",
                arguments={"path": str(outside)},
                caller="agent",
                hitl_approver=lambda s: False,
            )
            executor.invoke.assert_not_called()
            assert isinstance(result, ToolError)
            assert result.error_code == ToolErrorCode.HITL_BLOCKED
            assert "拒绝" in result.message
            # Denial leaves no lingering approval.
            with pytest.raises(ValueError, match="路径越界"):
                file_read(str(outside))
        finally:
            current_workspace.reset(token)
            clear_approved_paths()

    def test_no_approver_keeps_legacy_behavior(self, mock_get_hook, tmp_path, monkeypatch):
        from agentnexus.agents.tool_runner import execute_tool

        mock_get_hook.return_value.fire.return_value = _make_hook_ctx()
        ws = tmp_path / "ws"
        ws.mkdir()
        monkeypatch.chdir(ws)
        token = current_workspace.set(str(ws))
        try:
            clear_approved_paths()
            executor = _make_executor("ran")
            result = execute_tool(
                tool_executor=executor,
                name="file_read",
                arguments={"path": str(tmp_path / "outside" / "x.txt")},
                caller="agent",
                hitl_approver=None,
            )
            # No channel: no prompt attempted, tool invoked and its own
            # ValueError classification applies downstream as before.
            executor.invoke.assert_called_once()
            assert result == "ran"
        finally:
            current_workspace.reset(token)
            clear_approved_paths()

    def test_hitl_preapproved_consumed_once(self, mock_get_hook, tmp_path, monkeypatch):
        from agentnexus.agents.tool_runner import execute_tool

        mock_get_hook.return_value.fire.return_value = _make_hook_ctx()
        ws = tmp_path / "ws"
        ws.mkdir()
        monkeypatch.chdir(ws)
        token = current_workspace.set(str(ws))
        try:
            clear_approved_paths()
            calls: list[str] = []
            executor = _make_executor("written")

            def approver(summary: str) -> bool:
                calls.append(summary)
                return len(calls) == 1  # path prompt approved; later asks denied

            execute_tool(
                tool_executor=executor,
                name="file_write",
                arguments={"path": str(tmp_path / "outside" / "n.txt"),
                           "content": "hi"},
                caller="agent",
                hitl_approver=approver,
            )
            # Only the path prompt hit the user so far.
            assert len(calls) == 1
            # file_write's registry HITL gate received the routed approver:
            # the first ask is covered by the path approval (no new prompt)…
            _, kwargs = executor.invoke.call_args
            gate_approver = kwargs["hitl_approver"]
            assert gate_approver("registry gate summary") is True
            assert len(calls) == 1  # consumed — user not re-asked
            # …and only the NEXT ask falls through to the user.
            assert gate_approver("registry gate summary") is False
            assert len(calls) == 2
        finally:
            current_workspace.reset(token)
            clear_approved_paths()


class TestAllowedPathsConfig:
    def test_configured_root_accessible_without_prompt(self, tmp_path, monkeypatch):
        ws = tmp_path / "ws"
        lib = tmp_path / "shared-lib"
        ws.mkdir()
        lib.mkdir()
        (lib / "code.py").write_text("print(1)", encoding="utf-8")
        monkeypatch.chdir(ws)
        token = current_workspace.set(str(ws))
        try:
            clear_approved_paths()
            with pytest.raises(ValueError, match="路径越界"):
                _resolve_safe(str(lib / "code.py"))

            fake_settings = MagicMock()
            fake_settings.allowed_paths = [str(lib)]
            monkeypatch.setattr(
                "agentnexus.core.config.get_settings", lambda: fake_settings
            )
            assert _resolve_safe(str(lib / "code.py")) == lib / "code.py"
        finally:
            current_workspace.reset(token)
            clear_approved_paths()
