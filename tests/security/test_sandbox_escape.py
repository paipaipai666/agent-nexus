"""Security tests for sandbox backends in the shell tool.

Tests cover argument injection, binary detection, security flag construction,
profile construction, and temp directory behavior for the bubblewrap,
seatbelt, and docker backends of agentnexus.tools.shell.
"""

from unittest.mock import MagicMock, patch

import pytest

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

    @patch("agentnexus.tools.shell._docker_daemon_available", return_value=(True, ""))
    @patch("agentnexus.tools.shell._run_shell_command")
    @patch("agentnexus.tools.shell.shutil.which")
    def test_docker_security_flags(self, mock_which, mock_run, _mock_daemon):
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

    @patch("agentnexus.tools.shell._docker_daemon_available", return_value=(True, ""))
    @patch("agentnexus.tools.shell._run_shell_command")
    @patch("agentnexus.tools.shell.shutil.which")
    def test_docker_image_and_memory_config(self, mock_which, mock_run, _mock_daemon):
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
    @patch("agentnexus.tools.shell._docker_daemon_available", return_value=(True, ""))
    @patch("agentnexus.tools.shell._run_shell_command")
    @patch("agentnexus.tools.shell.shutil.which")
    def test_docker_no_user_flag_on_windows(self, mock_which, mock_run, _mock_daemon):
        """On Windows, --user flag should NOT be added."""
        mock_which.return_value = "/usr/bin/docker"
        mock_run.return_value = "ok"
        settings = MagicMock()
        settings.shell_execution_docker_image = "python:3.11-slim"
        settings.shell_execution_memory_mb = 256

        _execute_shell_docker("echo hi", "/tmp/work", settings, 30)

        cmd = mock_run.call_args[0][0]
        assert "--user" not in cmd


class TestTempDirSecurity:
    """Tests for temp directory prefix in the shell seatbelt backend."""

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
