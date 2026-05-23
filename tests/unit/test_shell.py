"""Tests for agentnexus.tools.shell."""

import subprocess
from unittest.mock import patch

import pytest

from agentnexus.tools.shell import _check_blacklist, get_os_info, shell_exec


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
        mock_run.return_value.stdout = "hello"
        mock_run.return_value.stderr = ""
        mock_run.return_value.returncode = 0

        result = shell_exec("echo hello")
        assert "hello" in result

    @patch("agentnexus.tools.shell.subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 30))
    @patch("agentnexus.tools.shell.get_settings")
    def test_timeout(self, mock_settings, mock_run):
        mock_settings.return_value.shell_enabled = True
        result = shell_exec("sleep 100")
        assert "超时" in result
