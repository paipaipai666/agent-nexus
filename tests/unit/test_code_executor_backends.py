"""Tests for code executor backend orchestration functions."""

import os
import subprocess
from unittest.mock import patch

import pytest

from agentnexus.tools.code_executor import (
    SandboxUnavailable,
    _execute_auto,
    _execute_bubblewrap,
    _execute_docker,
    _execute_e2b,
    _execute_locally,
    _execute_locally_with_warning,
    _execute_native_sandbox,
    _execute_seatbelt,
    _execute_windows_native,
    _format_completed_process,
    _has_e2b_key,
    _run_command,
    _unavailable_message,
    _disabled_message,
    python_execute,
)


class TestHasE2BKey:
    def test_no_attr(self):
        settings = object()
        assert _has_e2b_key(settings) is False

    def test_none_value(self):
        settings = type("Settings", (), {"e2b_api_key": None})()
        assert _has_e2b_key(settings) is False

    def test_empty_secret(self):
        from pydantic import SecretStr
        settings = type("Settings", (), {"e2b_api_key": SecretStr("")})()
        assert _has_e2b_key(settings) is False

    def test_valid_key(self):
        from pydantic import SecretStr
        settings = type("Settings", (), {"e2b_api_key": SecretStr("sk-valid")})()
        assert _has_e2b_key(settings) is True


class TestExecuteE2B:
    @patch("agentnexus.tools.code_executor.Sandbox", None)
    def test_sandbox_import_none_raises(self):
        from pydantic import SecretStr
        settings = type("Settings", (), {"e2b_api_key": SecretStr("sk-key")})()
        with pytest.raises(SandboxUnavailable, match="package is not available"):
            _execute_e2b("print(1)", settings)

    def test_env_key_set(self, mocker):
        from pydantic import SecretStr
        os.environ.pop("E2B_API_KEY", None)
        settings = type("Settings", (), {"e2b_api_key": SecretStr("sk-env")})()

        mock_sandbox_cls = mocker.patch("agentnexus.tools.code_executor.Sandbox")
        instance = mock_sandbox_cls.return_value.__enter__.return_value
        env_log = []

        def capture(code):
            env_log.append(os.environ.get("E2B_API_KEY"))
            return mocker.MagicMock(logs=mocker.MagicMock(stdout=[], stderr=[]), results=[])

        instance.run_code.side_effect = capture
        _execute_e2b("print(1)", settings)
        assert env_log[0] == "sk-env"
        assert os.environ.get("E2B_API_KEY") is None

    def test_env_key_restored(self, mocker):
        from pydantic import SecretStr
        os.environ["E2B_API_KEY"] = "previous"
        settings = type("Settings", (), {"e2b_api_key": SecretStr("sk-env")})()

        mock_sandbox_cls = mocker.patch("agentnexus.tools.code_executor.Sandbox")
        instance = mock_sandbox_cls.return_value.__enter__.return_value
        instance.run_code.return_value = mocker.MagicMock(
            logs=mocker.MagicMock(stdout=[], stderr=[]), results=[]
        )

        _execute_e2b("print(1)", settings)
        assert os.environ["E2B_API_KEY"] == "previous"
        os.environ.pop("E2B_API_KEY", None)

    def test_stdout_parsed(self, mocker):
        from pydantic import SecretStr
        settings = type("Settings", (), {"e2b_api_key": SecretStr("sk")})()

        mock_sandbox_cls = mocker.patch("agentnexus.tools.code_executor.Sandbox")
        instance = mock_sandbox_cls.return_value.__enter__.return_value
        instance.run_code.return_value = mocker.MagicMock(
            logs=mocker.MagicMock(stdout=["hello"], stderr=[]),
            results=[],
        )

        result = _execute_e2b("print('hello')", settings)
        assert "hello" in result

    def test_stderr_parsed(self, mocker):
        from pydantic import SecretStr
        settings = type("Settings", (), {"e2b_api_key": SecretStr("sk")})()

        mock_sandbox_cls = mocker.patch("agentnexus.tools.code_executor.Sandbox")
        instance = mock_sandbox_cls.return_value.__enter__.return_value
        instance.run_code.return_value = mocker.MagicMock(
            logs=mocker.MagicMock(stdout=[], stderr=["error"]),
            results=[],
        )

        result = _execute_e2b("import sys; sys.stderr.write('error')", settings)
        assert "error" in result

    def test_results_text(self, mocker):
        from pydantic import SecretStr
        settings = type("Settings", (), {"e2b_api_key": SecretStr("sk")})()

        mock_sandbox_cls = mocker.patch("agentnexus.tools.code_executor.Sandbox")
        instance = mock_sandbox_cls.return_value.__enter__.return_value

        res = mocker.MagicMock()
        res.text = "result_text"
        res.png = None
        res.json = None

        instance.run_code.return_value = mocker.MagicMock(
            logs=mocker.MagicMock(stdout=[], stderr=[]),
            results=[res],
        )

        result = _execute_e2b("print('x')", settings)
        assert "result_text" in result

    def test_results_png(self, mocker):
        from pydantic import SecretStr
        settings = type("Settings", (), {"e2b_api_key": SecretStr("sk")})()

        mock_sandbox_cls = mocker.patch("agentnexus.tools.code_executor.Sandbox")
        instance = mock_sandbox_cls.return_value.__enter__.return_value

        res = mocker.MagicMock()
        res.text = None
        res.png = b"PNG_DATA"
        res.json = None

        instance.run_code.return_value = mocker.MagicMock(
            logs=mocker.MagicMock(stdout=[], stderr=[]),
            results=[res],
        )

        result = _execute_e2b("print('x')", settings)
        assert "image output" in result

    def test_no_output(self, mocker):
        from pydantic import SecretStr
        settings = type("Settings", (), {"e2b_api_key": SecretStr("sk")})()

        mock_sandbox_cls = mocker.patch("agentnexus.tools.code_executor.Sandbox")
        instance = mock_sandbox_cls.return_value.__enter__.return_value
        instance.run_code.return_value = mocker.MagicMock(
            logs=mocker.MagicMock(stdout=[], stderr=[]),
            results=[],
        )

        result = _execute_e2b("x = 1", settings)
        assert "no output" in result


class TestExecuteAuto:
    @patch("agentnexus.tools.code_executor._execute_e2b")
    @patch("agentnexus.tools.code_executor._execute_native_sandbox")
    def test_tries_e2b_first(self, mock_native, mock_e2b):
        mock_e2b.return_value = "e2b ok"
        settings = type("Settings", (), {})()
        with patch("agentnexus.tools.code_executor._has_e2b_key", return_value=True):
            result = _execute_auto("print(1)", settings, 30)
        assert mock_e2b.called
        assert not mock_native.called
        assert "e2b" in result

    @patch("agentnexus.tools.code_executor._execute_e2b")
    @patch("agentnexus.tools.code_executor._execute_native_sandbox")
    @patch("agentnexus.tools.code_executor._execute_docker")
    @patch("agentnexus.tools.code_executor._execute_locally_with_warning")
    def test_no_key_skips_e2b(self, mock_local, mock_docker, mock_native, mock_e2b):
        mock_native.return_value = "native ok"
        settings = type("Settings", (), {})()
        with patch("agentnexus.tools.code_executor._has_e2b_key", return_value=False):
            result = _execute_auto("print(1)", settings, 30)
        assert not mock_e2b.called
        assert mock_native.called
        assert "native" in result

    @patch("agentnexus.tools.code_executor._execute_e2b")
    @patch("agentnexus.tools.code_executor._execute_native_sandbox")
    def test_e2b_fails_to_native(self, mock_native, mock_e2b):
        mock_e2b.side_effect = SandboxUnavailable("e2b down")
        mock_native.return_value = "native ok"
        settings = type("Settings", (), {})()
        with patch("agentnexus.tools.code_executor._has_e2b_key", return_value=True):
            result = _execute_auto("print(1)", settings, 30)
        assert mock_e2b.called
        assert mock_native.called
        assert "native" in result

    @patch("agentnexus.tools.code_executor._execute_e2b")
    @patch("agentnexus.tools.code_executor._execute_native_sandbox")
    @patch("agentnexus.tools.code_executor._execute_docker")
    @patch("agentnexus.tools.code_executor._execute_locally_with_warning")
    def test_all_fail_to_local_warning(self, mock_local, mock_docker, mock_native, mock_e2b):
        mock_e2b.side_effect = SandboxUnavailable("e2b")
        mock_native.side_effect = SandboxUnavailable("native")
        mock_docker.side_effect = SandboxUnavailable("docker")
        mock_local.return_value = "[warning]\nfallback"
        settings = type("Settings", (), {})()
        with patch("agentnexus.tools.code_executor._has_e2b_key", return_value=True):
            result = _execute_auto("print(1)", settings, 30)
        assert mock_local.called
        assert "warning" in result

    @patch("agentnexus.tools.code_executor._execute_e2b")
    @patch("agentnexus.tools.code_executor._execute_native_sandbox")
    def test_timeout_propagates(self, mock_native, mock_e2b):
        mock_e2b.side_effect = subprocess.TimeoutExpired(cmd="python", timeout=30)
        settings = type("Settings", (), {})()
        with patch("agentnexus.tools.code_executor._has_e2b_key", return_value=True):
            with pytest.raises(subprocess.TimeoutExpired):
                _execute_auto("print(1)", settings, 30)
        assert not mock_native.called


class TestExecuteNativeSandbox:
    @patch("agentnexus.tools.code_executor._SYSTEM", "Haiku")
    def test_unsupported_os(self):
        with pytest.raises(SandboxUnavailable, match="unsupported OS"):
            _execute_native_sandbox("print(1)", 30)

    @patch("agentnexus.tools.code_executor._SYSTEM", "Linux")
    @patch("agentnexus.tools.code_executor._execute_bubblewrap")
    def test_linux_bubblewrap(self, mock_bwrap):
        mock_bwrap.return_value = "bwrap ok"
        result = _execute_native_sandbox("print(1)", 30)
        assert "bwrap" in result

    @patch("agentnexus.tools.code_executor._SYSTEM", "Darwin")
    @patch("agentnexus.tools.code_executor._execute_seatbelt")
    def test_darwin_seatbelt(self, mock_seatbelt):
        mock_seatbelt.return_value = "seatbelt ok"
        result = _execute_native_sandbox("print(1)", 30)
        assert "seatbelt" in result

    @patch("agentnexus.tools.code_executor._SYSTEM", "Windows")
    @patch("agentnexus.tools.code_executor._execute_windows_native")
    def test_windows_windows_native(self, mock_win):
        mock_win.side_effect = SandboxUnavailable("Windows native not available")
        with pytest.raises(SandboxUnavailable):
            _execute_native_sandbox("print(1)", 30)


class TestExecuteBubblewrap:
    def test_bwrap_not_found(self):
        with patch("shutil.which", return_value=None):
            with pytest.raises(SandboxUnavailable, match="bubblewrap"):
                _execute_bubblewrap("print(1)", 30)

    def test_bwrap_found_and_executed(self, mocker):
        mocker.patch("shutil.which", return_value="/usr/bin/bwrap")
        mocker.patch("agentnexus.tools.code_executor._run_command", return_value="ok")
        result = _execute_bubblewrap("print(1)", 30)
        assert result == "ok"

    def test_bwrap_cmd_security_flags(self, mocker):
        mocker.patch("shutil.which", return_value="/usr/bin/bwrap")
        cmd_captured = []

        def capture(cmd, timeout, cwd=None):
            cmd_captured.extend(cmd)
            return "ok"

        mocker.patch("agentnexus.tools.code_executor._run_command", side_effect=capture)
        mocker.patch("pathlib.Path.write_text")

        _execute_bubblewrap("print(1)", 30)

        cmd_str = " ".join(cmd_captured)
        assert "--unshare-all" in cmd_str
        assert "--die-with-parent" in cmd_str
        assert "--ro-bind" in cmd_str
        assert "--tmpfs" in cmd_str
        assert "--proc" in cmd_str
        assert "--setenv" in cmd_str
        assert "PYTHONNOUSERSITE" in cmd_str


class TestExecuteSeatbelt:
    def test_seatbelt_not_found(self):
        with patch("shutil.which", return_value=None):
            with pytest.raises(SandboxUnavailable, match="sandbox-exec"):
                _execute_seatbelt("print(1)", 30)

    def test_seatbelt_found(self, mocker):
        mocker.patch("shutil.which", return_value="/usr/bin/sandbox-exec")
        mocker.patch("agentnexus.tools.code_executor._run_command", return_value="ok")
        mocker.patch("pathlib.Path.write_text")
        result = _execute_seatbelt("print(1)", 30)
        assert result == "ok"

    def test_seatbelt_profile_contains_deny(self, mocker):
        mocker.patch("shutil.which", return_value="/usr/bin/sandbox-exec")
        mocker.patch("agentnexus.tools.code_executor._run_command", return_value="ok")

        write_text_args = []

        def fake_write_text(code, encoding="utf-8"):
            write_text_args.append(code)
            return None

        mocker.patch("pathlib.Path.write_text", side_effect=fake_write_text)

        _execute_seatbelt("print(1)", 30)
        assert any("deny default" in str(a) for a in write_text_args)


class TestExecuteWindowsNative:
    def test_raises_sandbox_unavailable(self):
        with pytest.raises(SandboxUnavailable, match="Windows native"):
            _execute_windows_native("print(1)", 30)


class TestExecuteDocker:
    def test_docker_not_found(self):
        with patch("shutil.which", return_value=None):
            with pytest.raises(SandboxUnavailable, match="Docker CLI"):
                _execute_docker("print(1)", type("Settings", (), {"code_execution_docker_image": "python:3.11-slim", "code_execution_memory_mb": 256})(), 30)

    def test_docker_security_flags(self, mocker):
        mocker.patch("shutil.which", return_value="/usr/bin/docker")
        cmd_captured = []

        def capture(cmd, timeout, cwd=None):
            cmd_captured.extend(cmd)
            return "ok"

        mocker.patch("agentnexus.tools.code_executor._run_command", side_effect=capture)
        mocker.patch("pathlib.Path.write_text")

        _execute_docker("print(1)", type("Settings", (), {"code_execution_docker_image": "python:3.11-slim", "code_execution_memory_mb": 256})(), 30)

        cmd_str = " ".join(cmd_captured)
        assert "--network" in cmd_str and "none" in cmd_str
        assert "--read-only" in cmd_str
        assert "--cap-drop" in cmd_str and "ALL" in cmd_str
        assert "--security-opt" in cmd_str and "no-new-privileges" in cmd_str
        assert "--user" in cmd_str and "65534:65534" in cmd_str

    def test_docker_mount_read_only(self, mocker):
        mocker.patch("shutil.which", return_value="/usr/bin/docker")
        cmd_captured = []

        def capture(cmd, timeout, cwd=None):
            cmd_captured.extend(cmd)
            return "ok"

        mocker.patch("agentnexus.tools.code_executor._run_command", side_effect=capture)
        mocker.patch("pathlib.Path.write_text")

        _execute_docker("print(1)", type("Settings", (), {"code_execution_docker_image": "python:3.11-slim", "code_execution_memory_mb": 256})(), 30)

        cmd_str = " ".join(cmd_captured)
        assert "--network" in cmd_str and "none" in cmd_str
        assert "--read-only" in cmd_str
        assert "--cap-drop" in cmd_str and "ALL" in cmd_str
        assert "--security-opt" in cmd_str and "no-new-privileges" in cmd_str
        assert "--user" in cmd_str and "65534:65534" in cmd_str

    def test_docker_mount_read_only(self, mocker):
        mocker.patch("shutil.which", return_value="/usr/bin/docker")
        cmd_captured = []

        def capture(cmd, timeout, cwd=None):
            cmd_captured.extend(cmd)
            return "ok"

        mocker.patch("agentnexus.tools.code_executor._run_command", side_effect=capture)
        mocker.patch("pathlib.Path.write_text")

        _execute_docker("print(1)", type("Settings", (), {"code_execution_docker_image": "python:3.11-slim", "code_execution_memory_mb": 256})(), 30)

        mounts = [x for x in cmd_captured if ":ro" in x]
        assert len(mounts) > 0


class TestRunCommand:
    def test_success(self, mocker):
        mock_run = mocker.patch("subprocess.run")
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "hello"
        mock_run.return_value.stderr = ""
        result = _run_command(["python", "-c", "print(1)"], 30)
        assert "hello" in result

    def test_cwd_passed(self, mocker):
        mock_run = mocker.patch("subprocess.run")
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = ""
        mock_run.return_value.stderr = ""
        _run_command(["ls"], 30, cwd="/tmp")
        assert mock_run.call_count >= 1

    def test_timeout(self, mocker):
        mock_run = mocker.patch("subprocess.run")
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = ""
        mock_run.return_value.stderr = ""
        _run_command(["echo", "hi"], 60)
        assert mock_run.call_count >= 1


class TestFormatCompletedProcess:
    def test_error_no_output(self):
        result = type("CP", (), {"returncode": 1, "stdout": "", "stderr": ""})()
        output = _format_completed_process(result)
        assert "exit_code=1" in output

    def test_stdout(self):
        result = type("CP", (), {"returncode": 0, "stdout": "hello\n", "stderr": ""})()
        output = _format_completed_process(result)
        assert "hello" in output

    def test_stderr(self):
        result = type("CP", (), {"returncode": 0, "stdout": "", "stderr": "err"})()
        output = _format_completed_process(result)
        assert "err" in output

    def test_non_zero_with_output(self):
        result = type("CP", (), {"returncode": 1, "stdout": "out", "stderr": "err"})()
        output = _format_completed_process(result)
        assert "exit_code: 1" in output


class TestUnavailableMessage:
    def test_formats_failures(self):
        msg = _unavailable_message(["e2b: no key", "native: not found"])
        assert "e2b: no key" in msg
        assert "native: not found" in msg
        assert "[blocked]" in msg

    def test_empty_failures(self):
        msg = _unavailable_message([])
        assert "[blocked]" in msg


class TestDisabledMessage:
    def test_returns_blocked_message(self):
        msg = _disabled_message()
        assert "[blocked]" in msg
        assert "disabled" in msg


class TestExecuteLocallyWithWarning:
    def test_warning_before_result(self, mocker):
        mocker.patch("agentnexus.tools.code_executor._execute_locally", return_value="local ok")
        result = _execute_locally_with_warning("print(1)", 30, ["e2b: failed"])
        assert "[warning]" in result
        assert "local ok" in result

    def test_empty_local_result(self, mocker):
        mocker.patch("agentnexus.tools.code_executor._execute_locally", return_value="")
        result = _execute_locally_with_warning("print(1)", 30, ["e2b: failed"])
        assert "[warning]" in result
        assert "e2b" in result


class TestExecuteLocally:
    def test_executes_code(self, mocker):
        mock_run = mocker.patch("subprocess.run")
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "hello"
        mock_run.return_value.stderr = ""
        result = _execute_locally("print('hello')", 30)
        assert "hello" in result


class TestPythonExecBackendDispatch:
    @patch("agentnexus.tools.code_executor.get_settings")
    def test_backend_disabled(self, mock_settings):
        mock_settings.return_value.code_execution_backend = "disabled"
        result = python_execute("print(1)")
        assert "[blocked]" in result
        assert "disabled" in result

    @patch("agentnexus.tools.code_executor.get_settings")
    @patch("agentnexus.tools.code_executor._execute_auto")
    def test_backend_auto(self, mock_auto, mock_settings):
        mock_settings.return_value.code_execution_backend = "auto"
        mock_auto.return_value = "auto ok"
        result = python_execute("print(1)")
        assert mock_auto.called

    @patch("agentnexus.tools.code_executor.get_settings")
    @patch("agentnexus.tools.code_executor._execute_e2b")
    def test_backend_e2b(self, mock_e2b, mock_settings):
        mock_settings.return_value.code_execution_backend = "e2b"
        mock_e2b.return_value = "e2b ok"
        result = python_execute("print(1)")
        assert mock_e2b.called

    @patch("agentnexus.tools.code_executor.get_settings")
    @patch("agentnexus.tools.code_executor._execute_native_sandbox")
    def test_backend_native(self, mock_native, mock_settings):
        mock_settings.return_value.code_execution_backend = "native"
        mock_native.return_value = "native ok"
        result = python_execute("print(1)")
        assert mock_native.called

    @patch("agentnexus.tools.code_executor.get_settings")
    @patch("agentnexus.tools.code_executor._execute_docker")
    def test_backend_docker(self, mock_docker, mock_settings):
        mock_settings.return_value.code_execution_backend = "docker"
        mock_docker.return_value = "docker ok"
        result = python_execute("print(1)")
        assert mock_docker.called

    @patch("agentnexus.tools.code_executor.get_settings")
    @patch("agentnexus.tools.code_executor._execute_locally")
    def test_backend_local_unsafe_blocked(self, mock_local, mock_settings):
        mock_settings.return_value.code_execution_backend = "local_unsafe"
        mock_settings.return_value.code_execution_allow_unsafe_local = False
        result = python_execute("print(1)")
        assert "[blocked]" in result
        assert not mock_local.called

    @patch("agentnexus.tools.code_executor.get_settings")
    @patch("agentnexus.tools.code_executor._execute_locally")
    def test_backend_local_unsafe_allowed(self, mock_local, mock_settings):
        mock_settings.return_value.code_execution_backend = "local_unsafe"
        mock_settings.return_value.code_execution_allow_unsafe_local = True
        mock_local.return_value = "local ok"
        result = python_execute("print(1)")
        assert mock_local.called

    @patch("agentnexus.tools.code_executor.get_settings")
    def test_timeout_expired(self, mock_settings):
        mock_settings.return_value.code_execution_backend = "native"
        with patch("agentnexus.tools.code_executor._execute_native_sandbox",
                   side_effect=subprocess.TimeoutExpired(cmd="python", timeout=30)):
            result = python_execute("print(1)")
        assert "超时" in result

    @patch("agentnexus.tools.code_executor.get_settings")
    def test_sandbox_unavailable(self, mock_settings):
        mock_settings.return_value.code_execution_backend = "native"
        with patch("agentnexus.tools.code_executor._execute_native_sandbox",
                   side_effect=SandboxUnavailable("not found")):
            result = python_execute("print(1)")
        assert "[blocked]" in result

    @patch("agentnexus.tools.code_executor.get_settings")
    def test_generic_exception(self, mock_settings):
        mock_settings.return_value.code_execution_backend = "native"
        with patch("agentnexus.tools.code_executor._execute_native_sandbox",
                   side_effect=RuntimeError("boom")):
            result = python_execute("print(1)")
        assert "错误" in result

    @patch("agentnexus.tools.code_executor.get_settings")
    def test_unsupported_backend(self, mock_settings):
        mock_settings.return_value.code_execution_backend = "nonexistent"
        result = python_execute("print(1)")
        assert "[blocked]" in result
