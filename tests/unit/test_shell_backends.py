"""Tests for shell execution backend functions in agentnexus.tools.shell."""

import subprocess
from pathlib import Path

import pytest

from agentnexus.tools.shell import (
    _apply_timeout,
    _execute_shell_auto,
    _execute_shell_docker,
    _execute_shell_locally,
    _execute_shell_locally_with_warning,
    _execute_shell_native,
    _format_shell_result,
    _run_shell_command,
    _shell_unavailable_message,
    ShellSandboxUnavailable,
    shell_exec,
)


class TestApplyTimeout:
    def test_apply_timeout_windows(self, mocker):
        mocker.patch("agentnexus.tools.shell._SYSTEM", "Windows")
        assert _apply_timeout("echo hello", 30) == "echo hello"

    def test_apply_timeout_unix(self, mocker):
        mocker.patch("agentnexus.tools.shell._SYSTEM", "Linux")
        assert _apply_timeout("echo hello", 30) == "timeout 30 echo hello"

    def test_apply_timeout_zero(self, mocker):
        mocker.patch("agentnexus.tools.shell._SYSTEM", "Linux")
        assert _apply_timeout("echo hello", 0) == "timeout 0 echo hello"


class TestShellSandboxUnavailable:
    def test_exception_can_be_raised(self):
        try:
            raise ShellSandboxUnavailable("test error")
        except ShellSandboxUnavailable:
            pass

    def test_exception_is_runtime_error(self):
        assert issubclass(ShellSandboxUnavailable, RuntimeError)


class TestRunShellCommand:
    def test_run_shell_command_success(self, mocker):
        mock_run = mocker.patch("subprocess.run")
        mock_format = mocker.patch(
            "agentnexus.tools.shell._format_shell_result",
            return_value="formatted",
        )
        mock_run.return_value.stdout = "output"
        mock_run.return_value.stderr = ""
        mock_run.return_value.returncode = 0

        result = _run_shell_command(["echo", "hi"], timeout=30)

        mock_format.assert_called_once()
        assert result == "formatted"

    def test_run_shell_command_cwd(self, mocker):
        mock_run = mocker.patch("subprocess.run")
        mocker.patch("agentnexus.tools.shell._format_shell_result", return_value="")
        mock_run.return_value.stdout = ""
        mock_run.return_value.stderr = ""
        mock_run.return_value.returncode = 0

        _run_shell_command(["echo", "hi"], timeout=30, cwd="/tmp")

        assert mock_run.call_args[1].get("cwd") == "/tmp"

    def test_run_shell_command_timeout(self, mocker):
        mock_run = mocker.patch("subprocess.run")
        mocker.patch("agentnexus.tools.shell._format_shell_result", return_value="")
        mock_run.return_value.stdout = ""
        mock_run.return_value.stderr = ""
        mock_run.return_value.returncode = 0

        _run_shell_command(["echo", "hi"], timeout=60)

        assert mock_run.call_args[1].get("timeout") == 60

    def test_run_shell_command_timeout_expired(self, mocker):
        mocker.patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired("cmd", 30),
        )
        mocker.patch("agentnexus.tools.shell._format_shell_result", return_value="")

        with pytest.raises(subprocess.TimeoutExpired):
            _run_shell_command(["sleep", "100"], timeout=30)

    def test_run_shell_command_encoding(self, mocker):
        mock_run = mocker.patch("subprocess.run")
        mocker.patch("agentnexus.tools.shell._format_shell_result", return_value="")
        mock_run.return_value.stdout = ""
        mock_run.return_value.stderr = ""
        mock_run.return_value.returncode = 0

        _run_shell_command(["echo", "hi"], timeout=30)

        assert mock_run.call_args[1].get("encoding") == "utf-8"
        assert mock_run.call_args[1].get("errors") == "replace"


class TestFormatShellResult:
    @staticmethod
    def _make_result(stdout="", stderr="", returncode=0):
        return subprocess.CompletedProcess(
            args=[], returncode=returncode, stdout=stdout, stderr=stderr
        )

    def test_stdout_only(self):
        result = self._make_result(stdout="hello world")
        formatted = _format_shell_result(result)
        assert "[stdout]" in formatted
        assert "hello world" in formatted
        assert "[stderr]" not in formatted

    def test_stderr_only(self):
        result = self._make_result(stderr="error occurred")
        formatted = _format_shell_result(result)
        assert "[stderr]" in formatted
        assert "error occurred" in formatted
        assert "[stdout]" not in formatted

    def test_stdout_and_stderr(self):
        result = self._make_result(stdout="output", stderr="warning")
        formatted = _format_shell_result(result)
        assert "[stdout]" in formatted
        assert "[stderr]" in formatted
        assert "output" in formatted
        assert "warning" in formatted

    def test_no_output_success(self):
        result = self._make_result()
        formatted = _format_shell_result(result)
        assert "[执行完成，无输出]" in formatted

    def test_trailing_newlines_stripped(self):
        result = self._make_result(stdout="hello\n\n\n")
        formatted = _format_shell_result(result)
        assert "hello" in formatted
        assert not formatted.endswith("\n")

    def test_exit_code_included(self):
        result = self._make_result(returncode=42)
        formatted = _format_shell_result(result)
        assert "exit_code: 42" in formatted


class TestShellUnavailableMessage:
    def test_formats_failures(self):
        msg = _shell_unavailable_message(
            ["e2b: not available", "docker: not installed"]
        )
        assert "- e2b: not available" in msg
        assert "- docker: not installed" in msg

    def test_single_failure(self):
        msg = _shell_unavailable_message(["native: not supported"])
        assert "- native: not supported" in msg
        assert "No safe shell execution sandbox" in msg

    def test_mentions_auto_backend(self):
        msg = _shell_unavailable_message(["e2b: fail"])
        assert "auto" in msg
        assert "shell_execution_backend" in msg


class TestExecuteShellAuto:
    def test_auto_e2b_first(self, mocker):
        mock_e2b = mocker.patch(
            "agentnexus.tools.shell._execute_shell_e2b",
            return_value="e2b result",
        )
        mock_native = mocker.patch("agentnexus.tools.shell._execute_shell_native")
        mock_docker = mocker.patch("agentnexus.tools.shell._execute_shell_docker")
        mock_local = mocker.patch(
            "agentnexus.tools.shell._execute_shell_locally_with_warning"
        )
        settings = mocker.MagicMock()

        result = _execute_shell_auto("echo hi", "/tmp", settings, 30)

        assert result == "e2b result"
        mock_e2b.assert_called_once()
        mock_native.assert_not_called()
        mock_docker.assert_not_called()
        mock_local.assert_not_called()

    def test_auto_e2b_fails_falls_to_native(self, mocker):
        mock_e2b = mocker.patch(
            "agentnexus.tools.shell._execute_shell_e2b",
            side_effect=ShellSandboxUnavailable("e2b fail"),
        )
        mock_native = mocker.patch(
            "agentnexus.tools.shell._execute_shell_native",
            return_value="native result",
        )
        mock_docker = mocker.patch("agentnexus.tools.shell._execute_shell_docker")
        mock_local = mocker.patch(
            "agentnexus.tools.shell._execute_shell_locally_with_warning"
        )
        settings = mocker.MagicMock()

        result = _execute_shell_auto("echo hi", "/tmp", settings, 30)

        assert result == "native result"
        mock_e2b.assert_called_once()
        mock_native.assert_called_once()
        mock_docker.assert_not_called()
        mock_local.assert_not_called()

    def test_auto_all_fail_falls_to_local_with_warning(self, mocker):
        mock_e2b = mocker.patch(
            "agentnexus.tools.shell._execute_shell_e2b",
            side_effect=ShellSandboxUnavailable("e2b fail"),
        )
        mock_native = mocker.patch(
            "agentnexus.tools.shell._execute_shell_native",
            side_effect=ShellSandboxUnavailable("native fail"),
        )
        mock_docker = mocker.patch(
            "agentnexus.tools.shell._execute_shell_docker",
            side_effect=ShellSandboxUnavailable("docker fail"),
        )
        mock_local = mocker.patch(
            "agentnexus.tools.shell._execute_shell_locally_with_warning",
            return_value="local result",
        )
        settings = mocker.MagicMock()

        result = _execute_shell_auto("echo hi", "/tmp", settings, 30)

        assert result == "local result"
        mock_e2b.assert_called_once()
        mock_native.assert_called_once()
        mock_docker.assert_called_once()
        mock_local.assert_called_once_with(
            "echo hi",
            "/tmp",
            30,
            ["e2b: e2b fail", "native: native fail", "docker: docker fail"],
        )

    def test_auto_timeout_propagates(self, mocker):
        mock_e2b = mocker.patch(
            "agentnexus.tools.shell._execute_shell_e2b",
            side_effect=subprocess.TimeoutExpired("cmd", 30),
        )
        mock_native = mocker.patch("agentnexus.tools.shell._execute_shell_native")
        mock_docker = mocker.patch("agentnexus.tools.shell._execute_shell_docker")
        mock_local = mocker.patch(
            "agentnexus.tools.shell._execute_shell_locally_with_warning"
        )
        settings = mocker.MagicMock()

        with pytest.raises(subprocess.TimeoutExpired):
            _execute_shell_auto("sleep 100", "/tmp", settings, 30)

        mock_e2b.assert_called_once()
        mock_native.assert_not_called()
        mock_docker.assert_not_called()
        mock_local.assert_not_called()


class TestExecuteShellNative:
    def test_native_linux_bubblewrap(self, mocker):
        mock_bwrap = mocker.patch(
            "agentnexus.tools.shell._execute_shell_bubblewrap",
            return_value="bwrap result",
        )
        mock_seatbelt = mocker.patch("agentnexus.tools.shell._execute_shell_seatbelt")
        mock_windows = mocker.patch(
            "agentnexus.tools.shell._execute_shell_windows_native"
        )
        mocker.patch("agentnexus.tools.shell._SYSTEM", "Linux")

        result = _execute_shell_native("echo hi", "/tmp", 30)

        assert result == "bwrap result"
        mock_bwrap.assert_called_once_with("echo hi", "/tmp", 30)
        mock_seatbelt.assert_not_called()
        mock_windows.assert_not_called()

    def test_native_darwin_seatbelt(self, mocker):
        mock_bwrap = mocker.patch("agentnexus.tools.shell._execute_shell_bubblewrap")
        mock_seatbelt = mocker.patch(
            "agentnexus.tools.shell._execute_shell_seatbelt",
            return_value="seatbelt result",
        )
        mock_windows = mocker.patch(
            "agentnexus.tools.shell._execute_shell_windows_native"
        )
        mocker.patch("agentnexus.tools.shell._SYSTEM", "Darwin")

        result = _execute_shell_native("echo hi", "/tmp", 30)

        assert result == "seatbelt result"
        mock_bwrap.assert_not_called()
        mock_seatbelt.assert_called_once_with("echo hi", "/tmp", 30)
        mock_windows.assert_not_called()

    def test_native_windows_windows_native(self, mocker):
        mock_bwrap = mocker.patch("agentnexus.tools.shell._execute_shell_bubblewrap")
        mock_seatbelt = mocker.patch("agentnexus.tools.shell._execute_shell_seatbelt")
        mock_windows = mocker.patch(
            "agentnexus.tools.shell._execute_shell_windows_native",
            return_value="windows result",
        )
        mocker.patch("agentnexus.tools.shell._SYSTEM", "Windows")

        result = _execute_shell_native("echo hi", "/tmp", 30)

        assert result == "windows result"
        mock_bwrap.assert_not_called()
        mock_seatbelt.assert_not_called()
        mock_windows.assert_called_once_with("echo hi", "/tmp", 30)

    def test_native_unsupported_os(self, mocker):
        mocker.patch("agentnexus.tools.shell._execute_shell_bubblewrap")
        mocker.patch("agentnexus.tools.shell._execute_shell_seatbelt")
        mocker.patch("agentnexus.tools.shell._execute_shell_windows_native")
        mocker.patch("agentnexus.tools.shell._SYSTEM", "SunOS")

        with pytest.raises(ShellSandboxUnavailable, match="unsupported OS"):
            _execute_shell_native("echo hi", "/tmp", 30)


class TestExecuteShellDocker:
    def test_docker_not_installed(self, mocker):
        mocker.patch("agentnexus.tools.shell.shutil.which", return_value=None)
        settings = mocker.MagicMock()

        with pytest.raises(ShellSandboxUnavailable, match="Docker CLI"):
            _execute_shell_docker("echo hi", "/workspace", settings, 30)

    def test_docker_success(self, mocker):
        mocker.patch("agentnexus.tools.shell.shutil.which", return_value="/usr/bin/docker")
        mock_run = mocker.patch(
            "agentnexus.tools.shell._run_shell_command", return_value="ok"
        )
        settings = mocker.MagicMock()
        settings.shell_execution_docker_image = "python:3.11-slim"
        settings.shell_execution_memory_mb = 256

        result = _execute_shell_docker("echo ok", "/workspace", settings, 30)

        assert result == "ok"
        mock_run.assert_called_once()

    def test_docker_security_flags_present(self, mocker):
        mocker.patch("agentnexus.tools.shell.shutil.which", return_value="/usr/bin/docker")
        mock_run = mocker.patch(
            "agentnexus.tools.shell._run_shell_command", return_value="ok"
        )
        mocker.patch("agentnexus.tools.shell._SYSTEM", "Linux")
        mocker.patch("agentnexus.tools.shell.os.getuid", return_value=1000, create=True)
        mocker.patch("agentnexus.tools.shell.os.getgid", return_value=1000, create=True)
        settings = mocker.MagicMock()
        settings.shell_execution_docker_image = "python:3.11-slim"
        settings.shell_execution_memory_mb = 256

        _execute_shell_docker("echo ok", "/workspace", settings, 30)

        cmd = mock_run.call_args[0][0]
        assert "--network" in cmd
        assert "none" in cmd
        assert "--cap-drop" in cmd
        assert "ALL" in cmd
        assert "--security-opt" in cmd
        assert "no-new-privileges" in cmd
        assert "--pids-limit" in cmd
        assert "64" in cmd

    def test_docker_user_flag_on_unix(self, mocker):
        mocker.patch("agentnexus.tools.shell.shutil.which", return_value="/usr/bin/docker")
        mock_run = mocker.patch(
            "agentnexus.tools.shell._run_shell_command", return_value="ok"
        )
        mocker.patch("agentnexus.tools.shell._SYSTEM", "Linux")
        mocker.patch("agentnexus.tools.shell.os.getuid", return_value=1000, create=True)
        mocker.patch("agentnexus.tools.shell.os.getgid", return_value=1000, create=True)
        settings = mocker.MagicMock()
        settings.shell_execution_docker_image = "python:3.11-slim"
        settings.shell_execution_memory_mb = 256

        _execute_shell_docker("echo ok", "/workspace", settings, 30)

        cmd = mock_run.call_args[0][0]
        assert "--user" in cmd
        assert "1000:1000" in cmd

    def test_docker_no_user_flag_on_windows(self, mocker):
        mocker.patch("agentnexus.tools.shell.shutil.which", return_value="docker")
        mock_run = mocker.patch(
            "agentnexus.tools.shell._run_shell_command", return_value="ok"
        )
        mocker.patch("agentnexus.tools.shell._SYSTEM", "Windows")
        settings = mocker.MagicMock()
        settings.shell_execution_docker_image = "python:3.11-slim"
        settings.shell_execution_memory_mb = 256

        _execute_shell_docker("echo ok", "/workspace", settings, 30)

        cmd = mock_run.call_args[0][0]
        assert "--user" not in cmd


class TestExecuteShellLocally:
    def test_windows_uses_shell_true(self, mocker):
        mocker.patch("agentnexus.tools.shell._SYSTEM", "Windows")
        mock_run = mocker.patch("subprocess.run")
        mocker.patch("agentnexus.tools.shell._format_shell_result", return_value="ok")
        mock_run.return_value.stdout = ""
        mock_run.return_value.stderr = ""
        mock_run.return_value.returncode = 0

        _execute_shell_locally("echo hi", "C:\\tmp", 30)

        assert mock_run.call_args[1].get("shell") is True
        assert mock_run.call_args[0][0] == "echo hi"

    def test_unix_uses_sh_dash_lc(self, mocker):
        mocker.patch("agentnexus.tools.shell._SYSTEM", "Linux")
        mocker.patch("agentnexus.tools.shell.shutil.which", return_value="/bin/sh")
        mock_run = mocker.patch("subprocess.run")
        mocker.patch("agentnexus.tools.shell._format_shell_result", return_value="ok")
        mock_run.return_value.stdout = ""
        mock_run.return_value.stderr = ""
        mock_run.return_value.returncode = 0

        _execute_shell_locally("echo hi", "/tmp", 30)

        assert mock_run.call_args[0][0] == ["/bin/sh", "-lc", "echo hi"]

    def test_cwd_passed(self, mocker):
        mocker.patch("agentnexus.tools.shell._SYSTEM", "Windows")
        mock_run = mocker.patch("subprocess.run")
        mocker.patch("agentnexus.tools.shell._format_shell_result", return_value="ok")
        mock_run.return_value.stdout = ""
        mock_run.return_value.stderr = ""
        mock_run.return_value.returncode = 0

        _execute_shell_locally("echo hi", "/my/path", 30)

        assert mock_run.call_args[1].get("cwd") == "/my/path"

    def test_encoding(self, mocker):
        mocker.patch("agentnexus.tools.shell._SYSTEM", "Windows")
        mock_run = mocker.patch("subprocess.run")
        mocker.patch("agentnexus.tools.shell._format_shell_result", return_value="ok")
        mock_run.return_value.stdout = ""
        mock_run.return_value.stderr = ""
        mock_run.return_value.returncode = 0

        _execute_shell_locally("echo hi", "/tmp", 30)

        assert mock_run.call_args[1].get("encoding") == "utf-8"
        assert mock_run.call_args[1].get("errors") == "replace"


class TestExecuteShellLocallyWithWarning:
    def test_warning_formatted(self, mocker):
        mocker.patch(
            "agentnexus.tools.shell._execute_shell_locally", return_value=""
        )
        failures = ["e2b: fail", "native: fail"]

        result = _execute_shell_locally_with_warning(
            "echo hi", "/tmp", 30, failures
        )

        assert "[warning]" in result
        assert "unsafe local shell" in result
        assert "- e2b: fail" in result
        assert "- native: fail" in result

    def test_empty_result(self, mocker):
        mocker.patch(
            "agentnexus.tools.shell._execute_shell_locally", return_value=""
        )
        failures = ["e2b: fail"]

        result = _execute_shell_locally_with_warning(
            "echo hi", "/tmp", 30, failures
        )

        assert "[warning]" in result
        assert "shell execution sandboxes are unavailable" in result

    def test_with_result(self, mocker):
        mocker.patch(
            "agentnexus.tools.shell._execute_shell_locally",
            return_value="[stdout]\nhello\nexit_code: 0",
        )
        failures = ["e2b: fail"]

        result = _execute_shell_locally_with_warning(
            "echo hi", "/tmp", 30, failures
        )

        assert "[warning]" in result
        assert "[stdout]" in result
        assert "hello" in result


class TestShellExecBackendDispatch:
    def test_backend_disabled(self, mocker):
        settings = mocker.MagicMock()
        settings.shell_enabled = True
        settings.shell_execution_backend = "disabled"
        settings.shell_blacklist = []
        mocker.patch("agentnexus.tools.shell.get_settings", return_value=settings)
        mocker.patch("agentnexus.tools.shell._check_blacklist", return_value=None)

        result = shell_exec("echo hi")

        assert "disabled" in result.lower()

    def test_backend_auto(self, mocker):
        settings = mocker.MagicMock()
        settings.shell_enabled = True
        settings.shell_execution_backend = "auto"
        settings.shell_timeout = 30
        settings.shell_blacklist = []
        mocker.patch("agentnexus.tools.shell.get_settings", return_value=settings)
        mocker.patch("agentnexus.tools.shell._check_blacklist", return_value=None)
        mock_auto = mocker.patch(
            "agentnexus.tools.shell._execute_shell_auto", return_value="auto result"
        )
        mocker.patch("agentnexus.tools.file_ops._resolve_safe", return_value=Path("."))

        result = shell_exec("echo hi")

        assert result == "auto result"
        mock_auto.assert_called_once()

    def test_backend_e2b(self, mocker):
        settings = mocker.MagicMock()
        settings.shell_enabled = True
        settings.shell_execution_backend = "e2b"
        settings.shell_timeout = 30
        settings.shell_blacklist = []
        mocker.patch("agentnexus.tools.shell.get_settings", return_value=settings)
        mocker.patch("agentnexus.tools.shell._check_blacklist", return_value=None)
        mock_e2b = mocker.patch(
            "agentnexus.tools.shell._execute_shell_e2b", return_value="e2b result"
        )
        mocker.patch("agentnexus.tools.file_ops._resolve_safe", return_value=Path("."))

        result = shell_exec("echo hi")

        assert result == "e2b result"
        mock_e2b.assert_called_once()

    def test_backend_native(self, mocker):
        settings = mocker.MagicMock()
        settings.shell_enabled = True
        settings.shell_execution_backend = "native"
        settings.shell_timeout = 30
        settings.shell_blacklist = []
        mocker.patch("agentnexus.tools.shell.get_settings", return_value=settings)
        mocker.patch("agentnexus.tools.shell._check_blacklist", return_value=None)
        mock_native = mocker.patch(
            "agentnexus.tools.shell._execute_shell_native",
            return_value="native result",
        )
        mocker.patch("agentnexus.tools.file_ops._resolve_safe", return_value=Path("."))

        result = shell_exec("echo hi")

        assert result == "native result"
        mock_native.assert_called_once()

    def test_backend_docker(self, mocker):
        settings = mocker.MagicMock()
        settings.shell_enabled = True
        settings.shell_execution_backend = "docker"
        settings.shell_timeout = 30
        settings.shell_blacklist = []
        mocker.patch("agentnexus.tools.shell.get_settings", return_value=settings)
        mocker.patch("agentnexus.tools.shell._check_blacklist", return_value=None)
        mock_docker = mocker.patch(
            "agentnexus.tools.shell._execute_shell_docker",
            return_value="docker result",
        )
        mocker.patch("agentnexus.tools.file_ops._resolve_safe", return_value=Path("."))

        result = shell_exec("echo hi")

        assert result == "docker result"
        mock_docker.assert_called_once()

    def test_backend_local_unsafe(self, mocker):
        settings = mocker.MagicMock()
        settings.shell_enabled = True
        settings.shell_execution_backend = "local_unsafe"
        settings.shell_timeout = 30
        settings.shell_blacklist = []
        mocker.patch("agentnexus.tools.shell.get_settings", return_value=settings)
        mocker.patch("agentnexus.tools.shell._check_blacklist", return_value=None)
        mock_local = mocker.patch(
            "agentnexus.tools.shell._execute_shell_locally",
            return_value="local result",
        )
        mocker.patch("agentnexus.tools.file_ops._resolve_safe", return_value=Path("."))

        result = shell_exec("echo hi")

        assert result == "local result"
        mock_local.assert_called_once()

    def test_timeout_expired_handling(self, mocker):
        settings = mocker.MagicMock()
        settings.shell_enabled = True
        settings.shell_execution_backend = "local_unsafe"
        settings.shell_timeout = 30
        settings.shell_blacklist = []
        mocker.patch("agentnexus.tools.shell.get_settings", return_value=settings)
        mocker.patch("agentnexus.tools.shell._check_blacklist", return_value=None)
        mocker.patch(
            "agentnexus.tools.shell._execute_shell_locally",
            side_effect=subprocess.TimeoutExpired("cmd", 30),
        )
        mocker.patch("agentnexus.tools.file_ops._resolve_safe", return_value=Path("."))

        result = shell_exec("sleep 100")

        assert "超时" in result

    def test_file_not_found_handling(self, mocker):
        settings = mocker.MagicMock()
        settings.shell_enabled = True
        settings.shell_execution_backend = "local_unsafe"
        settings.shell_timeout = 30
        settings.shell_blacklist = []
        mocker.patch("agentnexus.tools.shell.get_settings", return_value=settings)
        mocker.patch("agentnexus.tools.shell._check_blacklist", return_value=None)
        mocker.patch(
            "agentnexus.tools.shell._execute_shell_locally",
            side_effect=FileNotFoundError(),
        )
        mocker.patch("agentnexus.tools.file_ops._resolve_safe", return_value=Path("."))

        result = shell_exec("nonexistent")

        assert "命令解释器未找到" in result

    def test_sandbox_unavailable_handling(self, mocker):
        settings = mocker.MagicMock()
        settings.shell_enabled = True
        settings.shell_execution_backend = "native"
        settings.shell_timeout = 30
        settings.shell_blacklist = []
        mocker.patch("agentnexus.tools.shell.get_settings", return_value=settings)
        mocker.patch("agentnexus.tools.shell._check_blacklist", return_value=None)
        mocker.patch(
            "agentnexus.tools.shell._execute_shell_native",
            side_effect=ShellSandboxUnavailable("no sandbox available"),
        )
        mocker.patch("agentnexus.tools.file_ops._resolve_safe", return_value=Path("."))

        result = shell_exec("echo hi")

        assert "blocked" in result
        assert "no sandbox available" in result.lower()

    def test_generic_exception_handling(self, mocker):
        settings = mocker.MagicMock()
        settings.shell_enabled = True
        settings.shell_execution_backend = "local_unsafe"
        settings.shell_timeout = 30
        settings.shell_blacklist = []
        mocker.patch("agentnexus.tools.shell.get_settings", return_value=settings)
        mocker.patch("agentnexus.tools.shell._check_blacklist", return_value=None)
        mocker.patch(
            "agentnexus.tools.shell._execute_shell_locally",
            side_effect=RuntimeError("some error"),
        )
        mocker.patch("agentnexus.tools.file_ops._resolve_safe", return_value=Path("."))

        result = shell_exec("echo hi")

        assert "错误" in result
        assert "some error" in result

    def test_unsupported_backend(self, mocker):
        settings = mocker.MagicMock()
        settings.shell_enabled = True
        settings.shell_execution_backend = "unknown_backend"
        settings.shell_timeout = 30
        settings.shell_blacklist = []
        mocker.patch("agentnexus.tools.shell.get_settings", return_value=settings)
        mocker.patch("agentnexus.tools.shell._check_blacklist", return_value=None)
        mocker.patch("agentnexus.tools.file_ops._resolve_safe", return_value=Path("."))

        result = shell_exec("echo hi")

        assert "blocked" in result
        assert "unsupported backend" in result
