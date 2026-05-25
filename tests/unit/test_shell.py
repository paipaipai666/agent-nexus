"""Tests for agentnexus.tools.shell."""

import subprocess
from unittest.mock import patch

from agentnexus.tools.shell import _check_blacklist, _execute_shell_docker, get_os_info, shell_exec


class TestCheckBlacklist:
    def test_blocks_shutdown(self):
        result = _check_blacklist("shutdown -s -t 0")
        assert result is not None
        assert "blocked" in result

    def test_blocks_reboot(self):
        result = _check_blacklist("reboot")
        assert result is not None

    def test_safe_command_passes(self):
        assert _check_blacklist("echo hello") is None

    def test_blocks_shutdown_variation(self):
        result = _check_blacklist("shutdown -h now")
        assert result is not None


class TestGetOsInfo:
    def test_returns_string(self):
        info = get_os_info()
        assert isinstance(info, str)
        assert len(info) > 0


class TestShellExec:
    @patch("agentnexus.tools.shell.get_settings")
    def test_disabled_returns_error(self, mock_settings):
        mock_settings.return_value.shell_enabled = False
        result = shell_exec("echo hi")
        assert "禁用" in result

    @patch("agentnexus.tools.shell._check_blacklist", return_value="[blocked] blocked")
    @patch("agentnexus.tools.shell.get_settings")
    def test_blocked_command(self, mock_settings, mock_blacklist):
        mock_settings.return_value.shell_enabled = True
        result = shell_exec("rm -rf /")
        assert "blocked" in result

    @patch("agentnexus.tools.shell.subprocess.run")
    @patch("agentnexus.tools.shell.get_settings")
    def test_successful_execution(self, mock_settings, mock_run):
        mock_settings.return_value.shell_enabled = True
        mock_settings.return_value.shell_execution_backend = "local_unsafe"
        mock_run.return_value.stdout = "hello"
        mock_run.return_value.stderr = ""
        mock_run.return_value.returncode = 0

        result = shell_exec("echo hello")
        assert "hello" in result

    @patch("agentnexus.tools.shell.subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 30))
    @patch("agentnexus.tools.shell.get_settings")
    def test_timeout(self, mock_settings, mock_run):
        mock_settings.return_value.shell_enabled = True
        mock_settings.return_value.shell_execution_backend = "local_unsafe"
        result = shell_exec("sleep 100")
        assert "超时" in result or "timeout" in result.lower()

    @patch("agentnexus.tools.shell.shutil.which")
    @patch("agentnexus.tools.shell.get_settings")
    def test_auto_warns_and_runs_local_when_no_safe_backend(self, mock_settings, mock_which):
        mock_settings.return_value.shell_enabled = True
        mock_settings.return_value.shell_execution_backend = "auto"
        mock_settings.return_value.shell_timeout = 30
        mock_which.return_value = None

        result = shell_exec("echo hello")

        assert "[warning]" in result
        assert "unsafe local shell" in result
        assert "hello" in result

    @patch("agentnexus.tools.shell.subprocess.run")
    @patch("agentnexus.tools.shell.shutil.which")
    def test_docker_backend_uses_restricted_container_flags(self, mock_which, mock_run):
        mock_which.return_value = "docker"
        mock_run.return_value.stdout = "ok"
        mock_run.return_value.stderr = ""
        mock_run.return_value.returncode = 0
        settings = type(
            "Settings",
            (),
            {
                "shell_execution_docker_image": "python:3.11-slim",
                "shell_execution_memory_mb": 256,
            },
        )()

        result = _execute_shell_docker("echo ok", "D:\\code\\AgentNexus", settings, timeout_sec=30)

        cmd = mock_run.call_args[0][0]
        assert "ok" in result
        assert "--network" in cmd and "none" in cmd
        assert "--cap-drop" in cmd and "ALL" in cmd
        assert "--security-opt" in cmd and "no-new-privileges" in cmd
