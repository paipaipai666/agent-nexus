"""Security tests for sandbox backends in executor tools.

Tests cover argument injection, binary detection, security flag construction,
profile construction, temp directory behavior, and cross-platform compatibility.
"""

import sys
from unittest.mock import MagicMock, patch

import pytest

from agentnexus.tools.code_executor import (
    SandboxUnavailable,
    _execute_bubblewrap,
    _execute_seatbelt,
)
from agentnexus.tools.shell import (
    ShellSandboxUnavailable,
    _execute_shell_bubblewrap,
    _execute_shell_docker,
    _execute_shell_seatbelt,
)


class TestBubblewrapShellSecurity:
    """Tests for shell.py bubblewrap sandbox backend (_execute_shell_bubblewrap)."""

    @patch("agentnexus.tools.shell._run_shell_command")
    @patch("agentnexus.tools.shell.shutil.which")
    def test_bwrap_command_argument_injection(self, mock_which, mock_run):
        """Crafted command injection should not alter bwrap argument list."""
        mock_run.return_value = "ok"
        mock_which.side_effect = ["/usr/bin/bwrap", "/bin/sh"]

        injected = "echo hi; --bind / /; --unshare-all; echo pwned"
        _execute_shell_bubblewrap(injected, "/tmp/work", 30)

        cmd = mock_run.call_args[0][0]
        assert cmd[-1] == injected
        assert cmd[-2] == "-lc"
        assert "--die-with-parent" in cmd
        bind_idx = cmd.index("--bind")
        assert cmd[bind_idx + 1] == "/tmp/work"
        assert cmd[bind_idx + 2] == "/workspace"

    @patch("agentnexus.tools.shell.shutil.which")
    def test_bwrap_shell_detection(self, mock_which):
        """Raises ShellSandboxUnavailable when bwrap binary is not found."""
        mock_which.return_value = None
        with pytest.raises(ShellSandboxUnavailable, match="bubblewrap is not installed"):
            _execute_shell_bubblewrap("echo hi", "/tmp/work", 30)

    @patch("agentnexus.tools.shell._run_shell_command")
    @patch("agentnexus.tools.shell.shutil.which")
    def test_bwrap_cmd_structure(self, mock_which, mock_run):
        """Constructed command list contains expected security flags."""
        mock_run.return_value = "ok"
        mock_which.side_effect = ["/usr/bin/bwrap", "/bin/sh"]

        _execute_shell_bubblewrap("echo hi", "/tmp/work", 30)

        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "/usr/bin/bwrap"
        assert "--unshare-all" in cmd
        assert "--die-with-parent" in cmd
        assert "--new-session" in cmd
        ro_bind_indices = [i for i, a in enumerate(cmd) if a == "--ro-bind"]
        ro_bind_pairs = [(cmd[i + 1], cmd[i + 2]) for i in ro_bind_indices]
        assert ("/usr", "/usr") in ro_bind_pairs
        assert ("/bin", "/bin") in ro_bind_pairs
        assert ("/lib", "/lib") in ro_bind_pairs
        assert ("/lib64", "/lib64") in ro_bind_pairs
        assert "--tmpfs" in cmd
        assert "/tmp" in cmd
        bind_idx = cmd.index("--bind")
        assert cmd[bind_idx + 1] == "/tmp/work"
        assert cmd[bind_idx + 2] == "/workspace"
        assert "--chdir" in cmd
        assert cmd[cmd.index("--chdir") + 1] == "/workspace"
        assert cmd[-3] == "/bin/sh"
        assert cmd[-2] == "-lc"
        assert cmd[-1] == "echo hi"

    @patch("agentnexus.tools.shell._run_shell_command")
    @patch("agentnexus.tools.shell.shutil.which")
    def test_bwrap_shell_fallback(self, mock_which, mock_run):
        """Falls back to /bin/sh when 'sh' is not on PATH."""
        mock_run.return_value = "ok"
        mock_which.side_effect = ["/usr/bin/bwrap", None]

        _execute_shell_bubblewrap("echo hi", "/tmp/work", 30)

        cmd = mock_run.call_args[0][0]
        assert cmd[cmd.index("-lc") - 1] == "/bin/sh"


class TestSeatbeltShellSecurity:
    """Tests for shell.py seatbelt sandbox backend (_execute_shell_seatbelt)."""

    @patch("agentnexus.tools.shell.shutil.which")
    def test_seatbelt_not_available(self, mock_which):
        """Raises ShellSandboxUnavailable when sandbox-exec is not found."""
        mock_which.return_value = None
        with pytest.raises(ShellSandboxUnavailable, match="sandbox-exec/Seatbelt is not available"):
            _execute_shell_seatbelt("echo hi", "/tmp/work", 30)

    @patch("agentnexus.tools.shell.Path.write_text")
    @patch("agentnexus.tools.shell._run_shell_command")
    @patch("agentnexus.tools.shell.shutil.which")
    def test_seatbelt_profile_construction(self, mock_which, mock_run, mock_write):
        """Profile contains deny-all with allow for system paths and work_dir."""
        mock_which.return_value = "/usr/bin/sandbox-exec"
        mock_run.return_value = "ok"
        _execute_shell_seatbelt("echo hi", "/tmp/work", 30)

        args, _ = mock_write.call_args
        content = args[0]
        assert "(version 1)" in content
        assert "(deny default)" in content
        assert "(allow process*)" in content
        assert '(allow file-read* (literal "/bin")' in content
        assert '(allow file-read* (subpath "/bin")' in content
        assert '(allow file-read* (subpath "/tmp/work"))' in content
        assert '(allow file-write* (subpath "/tmp/work"))' in content

    @patch("agentnexus.tools.shell._run_shell_command")
    @patch("agentnexus.tools.shell.shutil.which")
    def test_seatbelt_command_structure(self, mock_which, mock_run):
        """Command includes sandbox-exec with profile, shell, and user command."""
        mock_which.return_value = "/usr/bin/sandbox-exec"
        mock_run.return_value = "ok"
        _execute_shell_seatbelt("echo hi", "/tmp/work", 30)

        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "/usr/bin/sandbox-exec"
        assert cmd[1] == "-f"
        assert cmd[3] == "/bin/sh"
        assert cmd[4] == "-lc"
        assert cmd[5] == "echo hi"


class TestDockerShellSecurity:
    """Tests for shell.py docker sandbox backend (_execute_shell_docker)."""

    @patch("agentnexus.tools.shell.shutil.which")
    def test_docker_not_available(self, mock_which):
        """Raises ShellSandboxUnavailable when docker CLI is not on PATH."""
        mock_which.return_value = None
        settings = MagicMock()
        with pytest.raises(ShellSandboxUnavailable, match="Docker CLI is not installed"):
            _execute_shell_docker("echo hi", "/tmp/work", settings, 30)

    @patch("agentnexus.tools.shell._run_shell_command")
    @patch("agentnexus.tools.shell.shutil.which")
    def test_docker_security_flags(self, mock_which, mock_run):
        """Docker command contains security restriction flags."""
        mock_which.return_value = "/usr/bin/docker"
        mock_run.return_value = "ok"
        settings = MagicMock()
        settings.shell_execution_docker_image = "python:3.11-slim"
        settings.shell_execution_memory_mb = 256

        _execute_shell_docker("echo hi", "/tmp/work", settings, 30)

        cmd = mock_run.call_args[0][0]
        assert "--network" in cmd
        assert "none" in cmd[cmd.index("--network") + 1]
        assert "--cpus" in cmd
        assert cmd[cmd.index("--cpus") + 1] == "1"
        assert "--memory" in cmd
        assert "--pids-limit" in cmd
        assert cmd[cmd.index("--pids-limit") + 1] == "64"
        assert "--cap-drop" in cmd
        assert cmd[cmd.index("--cap-drop") + 1] == "ALL"
        assert "--security-opt" in cmd
        assert cmd[cmd.index("--security-opt") + 1] == "no-new-privileges"

    @patch("agentnexus.tools.shell._run_shell_command")
    @patch("agentnexus.tools.shell.shutil.which")
    def test_docker_image_and_memory_config(self, mock_which, mock_run):
        """Uses configured docker image and memory settings."""
        mock_which.return_value = "/usr/bin/docker"
        mock_run.return_value = "ok"
        settings = MagicMock()
        settings.shell_execution_docker_image = "custom:latest"
        settings.shell_execution_memory_mb = 512

        _execute_shell_docker("echo hi", "/tmp/work", settings, 30)

        cmd = mock_run.call_args[0][0]
        memory_idx = cmd.index("--memory")
        assert cmd[memory_idx + 1] == "512m"
        assert "custom:latest" in cmd

    @patch("agentnexus.tools.shell._SYSTEM", "Windows")
    @patch("agentnexus.tools.shell._run_shell_command")
    @patch("agentnexus.tools.shell.shutil.which")
    def test_docker_no_user_flag_on_windows(self, mock_which, mock_run):
        """On Windows, --user flag should NOT be added."""
        mock_which.return_value = "/usr/bin/docker"
        mock_run.return_value = "ok"
        settings = MagicMock()
        settings.shell_execution_docker_image = "python:3.11-slim"
        settings.shell_execution_memory_mb = 256

        _execute_shell_docker("echo hi", "/tmp/work", settings, 30)

        cmd = mock_run.call_args[0][0]
        assert "--user" not in cmd


class TestBubblewrapCodeSecurity:
    """Tests for code_executor.py bubblewrap sandbox backend (_execute_bubblewrap)."""

    @patch("agentnexus.tools.code_executor.shutil.which")
    def test_code_bwrap_not_found(self, mock_which):
        """Raises SandboxUnavailable when bwrap is not found."""
        mock_which.return_value = None
        with pytest.raises(SandboxUnavailable, match="bubblewrap is not installed"):
            _execute_bubblewrap("print('hi')", 30)

    @patch("agentnexus.tools.code_executor._run_command")
    @patch("agentnexus.tools.code_executor.shutil.which")
    def test_code_bwrap_cmd_structure(self, mock_which, mock_run):
        """Command contains bubblewrap security flags for code execution."""
        mock_which.return_value = "/usr/bin/bwrap"
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = ""
        mock_run.return_value.stderr = ""

        _execute_bubblewrap("print('hi')", 30)

        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "/usr/bin/bwrap"
        assert "--unshare-all" in cmd
        assert "--die-with-parent" in cmd
        assert "--new-session" in cmd
        ro_bind_indices = [i for i, a in enumerate(cmd) if a == "--ro-bind"]
        ro_bind_pairs = [(cmd[i + 1], cmd[i + 2]) for i in ro_bind_indices]
        assert sys.executable in [p[0] for p in ro_bind_pairs]
        assert ("/usr", "/usr") in ro_bind_pairs
        assert ("/lib", "/lib") in ro_bind_pairs
        assert ("/lib64", "/lib64") in ro_bind_pairs
        assert "--proc" in cmd
        assert "/proc" in cmd[cmd.index("--proc") + 1]
        assert "--dev" in cmd
        assert "/dev" in cmd[cmd.index("--dev") + 1]
        assert "--tmpfs" in cmd
        assert "/tmp" in cmd[cmd.index("--tmpfs") + 1]
        env_idx = cmd.index("--setenv")
        assert cmd[env_idx + 1] == "PYTHONNOUSERSITE"
        assert cmd[env_idx + 2] == "1"
        assert cmd[-1] == "/workspace/main.py"

    @patch("agentnexus.tools.code_executor._run_command")
    @patch("agentnexus.tools.code_executor.shutil.which")
    def test_code_bwrap_script_written(self, mock_which, mock_run):
        """Code is written to a temp file and mounted as read-only."""
        mock_which.return_value = "/usr/bin/bwrap"
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = ""
        mock_run.return_value.stderr = ""

        _execute_bubblewrap("print('hi')", 30)

        cmd = mock_run.call_args[0][0]
        for i, arg in enumerate(cmd):
            if arg == "--ro-bind" and i + 2 < len(cmd) and cmd[i + 2] == "/workspace/main.py":
                assert cmd[i + 1].endswith("main.py")
                break
        else:
            pytest.fail("--ro-bind for /workspace/main.py not found in command")


class TestSeatbeltCodeSecurity:
    """Tests for code_executor.py seatbelt sandbox backend (_execute_seatbelt)."""

    @patch("agentnexus.tools.code_executor.shutil.which")
    def test_code_seatbelt_not_found(self, mock_which):
        """Raises SandboxUnavailable when sandbox-exec is not found."""
        mock_which.return_value = None
        with pytest.raises(SandboxUnavailable, match="sandbox-exec/Seatbelt is not available"):
            _execute_seatbelt("print('hi')", 30)

    @patch("agentnexus.tools.code_executor.Path.write_text")
    @patch("agentnexus.tools.code_executor._run_command")
    @patch("agentnexus.tools.code_executor.shutil.which")
    def test_code_seatbelt_profile_construction(self, mock_which, mock_run, mock_write):
        """Profile contains deny-all with allow for system paths."""
        mock_which.return_value = "/usr/bin/sandbox-exec"
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = ""
        mock_run.return_value.stderr = ""

        _execute_seatbelt("print('hi')", 30)

        profile_content = None
        for call_obj in mock_write.call_args_list:
            content = call_obj.args[0]
            if "(deny default)" in content:
                profile_content = content
                break
        assert profile_content is not None, "Profile content not found"
        assert "(version 1)" in profile_content
        assert "(deny default)" in profile_content
        assert "(allow process*)" in profile_content
        assert '(allow file-read* (literal "/usr")' in profile_content
        assert '(allow file-read* (subpath "/usr")' in profile_content

    @patch("agentnexus.tools.code_executor._run_command")
    @patch("agentnexus.tools.code_executor.shutil.which")
    def test_code_seatbelt_command_structure(self, mock_which, mock_run):
        """Command has sandbox-exec, -f, profile, python, and script."""
        mock_which.return_value = "/usr/bin/sandbox-exec"
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = ""
        mock_run.return_value.stderr = ""

        _execute_seatbelt("print('hi')", 30)

        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "/usr/bin/sandbox-exec"
        assert cmd[1] == "-f"
        assert cmd[3] == sys.executable
        assert cmd[4].endswith("main.py")


class TestTempDirSecurity:
    """Tests for temp directory prefix and script encoding."""

    @patch("agentnexus.tools.code_executor.Path.write_text")
    @patch("agentnexus.tools.code_executor._run_command")
    @patch("agentnexus.tools.code_executor.shutil.which")
    def test_code_executor_temp_dir_prefix(self, mock_which, mock_run, mock_write):
        """Temp dir prefix is 'agentnexus-code-' for code executor."""
        mock_which.return_value = "/usr/bin/bwrap"
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = ""
        mock_run.return_value.stderr = ""

        with patch("agentnexus.tools.code_executor.tempfile.TemporaryDirectory") as mock_tmp:
            mock_instance = MagicMock()
            mock_instance.__enter__.return_value = "C:\\tmp\\agentnexus-code-xxx"
            mock_tmp.return_value = mock_instance
            _execute_bubblewrap("print('hi')", 30)

        mock_tmp.assert_called_once_with(prefix="agentnexus-code-")

    @patch("agentnexus.tools.shell.Path.write_text")
    @patch("agentnexus.tools.shell._run_shell_command")
    @patch("agentnexus.tools.shell.shutil.which")
    def test_shell_seatbelt_temp_dir_prefix(self, mock_which, mock_run, mock_write):
        """Temp dir prefix is 'agentnexus-shell-' for shell seatbelt."""
        mock_which.return_value = "/usr/bin/sandbox-exec"
        mock_run.return_value = "ok"
        with patch("agentnexus.tools.shell.tempfile.TemporaryDirectory") as mock_tmp:
            mock_instance = MagicMock()
            mock_instance.__enter__.return_value = "C:\\tmp\\agentnexus-shell-xxx"
            mock_tmp.return_value = mock_instance
            _execute_shell_seatbelt("echo hi", "/tmp/work", 30)

        mock_tmp.assert_called_once_with(prefix="agentnexus-shell-")

    @patch("agentnexus.tools.code_executor._run_command")
    @patch("agentnexus.tools.code_executor.shutil.which")
    def test_script_file_written_with_utf8(self, mock_which, mock_run):
        """Script is written with utf-8 encoding."""
        mock_which.return_value = "/usr/bin/bwrap"
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = ""
        mock_run.return_value.stderr = ""

        with patch("agentnexus.tools.code_executor.Path.write_text") as mock_write:
            _execute_bubblewrap("print('hi')", 30)

        mock_write.assert_called_once()
        _, kwargs = mock_write.call_args
        assert kwargs.get("encoding") == "utf-8"
