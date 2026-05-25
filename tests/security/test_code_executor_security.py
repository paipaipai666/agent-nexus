"""Security tests for code executor backend orchestration.

Covers credential detection (E2B key), sandbox availability gating,
env var isolation, backend routing policy, timeout propagation,
and OS-level sandbox dispatch.
"""

import os
import subprocess
from unittest.mock import patch

import pytest

from agentnexus.tools.code_executor import (
    SandboxUnavailable,
    _execute_auto,
    _execute_docker,
    _execute_e2b,
    _execute_native_sandbox,
    _has_e2b_key,
    python_execute,
)


class TestHasE2BKey:
    """_has_e2b_key detects E2B API key presence correctly."""

    def test_no_e2b_key_attr(self):
        """Settings without e2b_api_key attribute returns False."""
        settings = object()
        assert _has_e2b_key(settings) is False

    def test_e2b_key_none(self):
        """e2b_api_key=None returns False."""
        settings = type("Settings", (), {"e2b_api_key": None})()
        assert _has_e2b_key(settings) is False

    def test_e2b_key_empty(self):
        """e2b_api_key with empty secret value returns False."""
        from pydantic import SecretStr
        settings = type("Settings", (), {"e2b_api_key": SecretStr("")})()
        assert _has_e2b_key(settings) is False

    def test_e2b_key_present(self):
        """Returns True when key has a secret value."""
        from pydantic import SecretStr
        settings = type("Settings", (), {"e2b_api_key": SecretStr("sk-real-key")})()
        assert _has_e2b_key(settings) is True


class TestExecuteE2BSecurity:
    """_execute_e2b validates credentials, sandbox availability, and env isolation."""

    def test_e2b_key_not_configured_raises(self):
        """Raises SandboxUnavailable when api key secret value is empty."""
        from pydantic import SecretStr
        settings = type("Settings", (), {"e2b_api_key": SecretStr("")})()
        with pytest.raises(SandboxUnavailable, match="not configured"):
            _execute_e2b("print(1)", settings)

    def test_e2b_sandbox_not_available(self, mocker):
        """Raises SandboxUnavailable when Sandbox import failed (Sandbox is None)."""
        from pydantic import SecretStr
        settings = type("Settings", (), {"e2b_api_key": SecretStr("sk-key")})()
        mocker.patch("agentnexus.tools.code_executor.Sandbox", None)
        with pytest.raises(SandboxUnavailable, match="package is not available"):
            _execute_e2b("print(1)", settings)

    def test_e2b_api_key_set_in_env(self, mocker):
        """API key is set in os.environ['E2B_API_KEY'] during execution."""
        from pydantic import SecretStr
        os.environ.pop("E2B_API_KEY", None)
        settings = type("Settings", (), {"e2b_api_key": SecretStr("sk-env-key")})()

        mock_sandbox_cls = mocker.patch("agentnexus.tools.code_executor.Sandbox")
        sandbox_instance = mock_sandbox_cls.return_value.__enter__.return_value
        env_values = []

        def capture_env(code):
            env_values.append(os.environ.get("E2B_API_KEY"))
            return mocker.MagicMock(
                logs=mocker.MagicMock(stdout=[], stderr=[]), results=[]
            )

        sandbox_instance.run_code.side_effect = capture_env

        _execute_e2b("print(1)", settings)
        assert env_values[0] == "sk-env-key"
        assert os.environ.get("E2B_API_KEY") is None

    def test_e2b_api_key_restored(self, mocker):
        """Original env var is restored after execution."""
        from pydantic import SecretStr
        os.environ["E2B_API_KEY"] = "previous-key"
        settings = type("Settings", (), {"e2b_api_key": SecretStr("sk-env-key")})()

        mock_sandbox_cls = mocker.patch("agentnexus.tools.code_executor.Sandbox")
        sandbox_instance = mock_sandbox_cls.return_value.__enter__.return_value
        sandbox_instance.run_code.return_value = mocker.MagicMock(
            logs=mocker.MagicMock(stdout=[], stderr=[]), results=[]
        )

        _execute_e2b("print(1)", settings)
        assert os.environ["E2B_API_KEY"] == "previous-key"
        os.environ.pop("E2B_API_KEY", None)

    def test_e2b_api_key_cleaned(self, mocker):
        """When no previous key, E2B_API_KEY is removed from env after execution."""
        from pydantic import SecretStr
        os.environ.pop("E2B_API_KEY", None)
        settings = type("Settings", (), {"e2b_api_key": SecretStr("sk-env-key")})()

        mock_sandbox_cls = mocker.patch("agentnexus.tools.code_executor.Sandbox")
        sandbox_instance = mock_sandbox_cls.return_value.__enter__.return_value
        sandbox_instance.run_code.return_value = mocker.MagicMock(
            logs=mocker.MagicMock(stdout=[], stderr=[]), results=[]
        )

        _execute_e2b("print(1)", settings)
        assert os.environ.get("E2B_API_KEY") is None


class TestExecuteAutoSecurity:
    """_execute_auto skips, falls through, or propagates correctly."""

    @patch("agentnexus.tools.code_executor._execute_e2b")
    @patch("agentnexus.tools.code_executor._execute_native_sandbox")
    @patch("agentnexus.tools.code_executor._execute_docker")
    @patch("agentnexus.tools.code_executor._execute_locally_with_warning")
    def test_auto_no_e2b_key_skips_e2b(
        self, mock_local, mock_docker, mock_native, mock_e2b
    ):
        """When _has_e2b_key returns False, e2b is skipped and native is tried."""
        mock_native.return_value = "native ok"
        settings = type("Settings", (), {})()

        with patch("agentnexus.tools.code_executor._has_e2b_key", return_value=False):
            result = _execute_auto("print(1)", settings, timeout=30)

        assert not mock_e2b.called
        assert mock_native.called
        assert "native" in result

    @patch("agentnexus.tools.code_executor._execute_e2b")
    @patch("agentnexus.tools.code_executor._execute_native_sandbox")
    @patch("agentnexus.tools.code_executor._execute_docker")
    @patch("agentnexus.tools.code_executor._execute_locally_with_warning")
    def test_auto_e2b_key_sandbox_not_available(
        self, mock_local, mock_docker, mock_native, mock_e2b
    ):
        """Has key but sandbox unavailable, falls through to native."""
        mock_e2b.side_effect = SandboxUnavailable("e2b not available")
        mock_native.return_value = "native ok"
        settings = type("Settings", (), {})()

        with patch("agentnexus.tools.code_executor._has_e2b_key", return_value=True):
            result = _execute_auto("print(1)", settings, timeout=30)

        assert mock_e2b.called
        assert mock_native.called
        assert not mock_docker.called
        assert not mock_local.called
        assert "native" in result

    @patch("agentnexus.tools.code_executor._execute_e2b")
    @patch("agentnexus.tools.code_executor._execute_native_sandbox")
    @patch("agentnexus.tools.code_executor._execute_docker")
    @patch("agentnexus.tools.code_executor._execute_locally_with_warning")
    def test_auto_all_backends_fail(
        self, mock_local, mock_docker, mock_native, mock_e2b
    ):
        """All backends fail, falls through to _execute_locally_with_warning."""
        mock_e2b.side_effect = SandboxUnavailable("e2b down")
        mock_native.side_effect = SandboxUnavailable("native down")
        mock_docker.side_effect = SandboxUnavailable("docker down")
        mock_local.return_value = "[warning]\nlocal fallback"
        settings = type("Settings", (), {})()

        with patch("agentnexus.tools.code_executor._has_e2b_key", return_value=True):
            result = _execute_auto("print(1)", settings, timeout=30)

        assert mock_e2b.called
        assert mock_native.called
        assert mock_docker.called
        assert mock_local.called
        assert "warning" in result

    @patch("agentnexus.tools.code_executor._execute_e2b")
    @patch("agentnexus.tools.code_executor._execute_native_sandbox")
    @patch("agentnexus.tools.code_executor._execute_docker")
    @patch("agentnexus.tools.code_executor._execute_locally_with_warning")
    def test_auto_timeout_propagates_from_e2b(
        self, mock_local, mock_docker, mock_native, mock_e2b
    ):
        """TimeoutExpired from e2b propagates immediately."""
        mock_e2b.side_effect = subprocess.TimeoutExpired(cmd="python", timeout=30)
        settings = type("Settings", (), {})()

        with patch("agentnexus.tools.code_executor._has_e2b_key", return_value=True):
            with pytest.raises(subprocess.TimeoutExpired):
                _execute_auto("print(1)", settings, timeout=30)

        assert mock_e2b.called
        assert not mock_native.called
        assert not mock_docker.called
        assert not mock_local.called

    @patch("agentnexus.tools.code_executor._execute_e2b")
    @patch("agentnexus.tools.code_executor._execute_native_sandbox")
    @patch("agentnexus.tools.code_executor._execute_docker")
    @patch("agentnexus.tools.code_executor._execute_locally_with_warning")
    def test_auto_timeout_propagates_from_native(
        self, mock_local, mock_docker, mock_native, mock_e2b
    ):
        """TimeoutExpired from native propagates immediately."""
        mock_e2b.side_effect = SandboxUnavailable("e2b down")
        mock_native.side_effect = subprocess.TimeoutExpired(cmd="python", timeout=30)
        settings = type("Settings", (), {})()

        with patch("agentnexus.tools.code_executor._has_e2b_key", return_value=True):
            with pytest.raises(subprocess.TimeoutExpired):
                _execute_auto("print(1)", settings, timeout=30)

        assert mock_e2b.called
        assert mock_native.called
        assert not mock_docker.called
        assert not mock_local.called

    @patch("agentnexus.tools.code_executor._execute_e2b")
    @patch("agentnexus.tools.code_executor._execute_native_sandbox")
    @patch("agentnexus.tools.code_executor._execute_docker")
    @patch("agentnexus.tools.code_executor._execute_locally_with_warning")
    def test_auto_timeout_propagates_from_docker(
        self, mock_local, mock_docker, mock_native, mock_e2b
    ):
        """TimeoutExpired from docker propagates immediately."""
        mock_e2b.side_effect = SandboxUnavailable("e2b down")
        mock_native.side_effect = SandboxUnavailable("native down")
        mock_docker.side_effect = subprocess.TimeoutExpired(cmd="docker", timeout=30)
        settings = type("Settings", (), {})()

        with patch("agentnexus.tools.code_executor._has_e2b_key", return_value=True):
            with pytest.raises(subprocess.TimeoutExpired):
                _execute_auto("print(1)", settings, timeout=30)

        assert mock_e2b.called
        assert mock_native.called
        assert mock_docker.called
        assert not mock_local.called


class TestCodeExecutionBackendCompliance:
    """python_execute enforces backend policy correctly."""

    @patch("agentnexus.tools.code_executor.get_settings")
    def test_disabled_backend(self, mock_settings):
        """python_execute with code_execution_backend=disabled returns blocked."""
        mock_settings.return_value.code_execution_backend = "disabled"
        mock_settings.return_value.code_execution_timeout = 30
        result = python_execute("print('hello')")
        assert "[blocked]" in result
        assert "disabled" in result

    @patch("agentnexus.tools.code_executor.get_settings")
    def test_local_unsafe_without_allow(self, mock_settings):
        """backend=local_unsafe with allow_unsafe_local=False returns blocked."""
        mock_settings.return_value.code_execution_backend = "local_unsafe"
        mock_settings.return_value.code_execution_allow_unsafe_local = False
        result = python_execute("print('hello')")
        assert "[blocked]" in result
        assert "local_unsafe" in result

    @patch("agentnexus.tools.code_executor.get_settings")
    @patch("agentnexus.tools.code_executor._execute_locally")
    def test_local_unsafe_with_allow(self, mock_local, mock_settings):
        """allow_unsafe_local=True calls _execute_locally."""
        mock_settings.return_value.code_execution_backend = "local_unsafe"
        mock_settings.return_value.code_execution_allow_unsafe_local = True
        mock_settings.return_value.code_execution_timeout = 30
        mock_local.return_value = "local ok"
        result = python_execute("print('hello')")
        assert mock_local.called
        assert "local ok" in result

    @patch("agentnexus.tools.code_executor.get_settings")
    def test_backend_auto_default_config(self, mock_settings):
        """Default config uses auto backend (no sandbox available → warning)."""
        mock_settings.return_value.code_execution_backend = "auto"
        mock_settings.return_value.code_execution_timeout = 30
        mock_settings.return_value.e2b_api_key.get_secret_value.return_value = ""
        with patch("agentnexus.tools.code_executor.shutil.which", return_value=None):
            result = python_execute("print('hello')")
        assert "[warning]" in result

    @patch("agentnexus.tools.code_executor.shutil.which")
    @patch("agentnexus.tools.code_executor.subprocess.run")
    def test_docker_backend_security_flags(self, mock_run, mock_which):
        """docker cmd includes --network none, --read-only, --cap-drop ALL, --security-opt no-new-privileges, --user 65534:65534."""  # noqa: E501
        mock_which.return_value = "docker"
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "ok"
        mock_run.return_value.stderr = ""

        settings = type(
            "Settings",
            (),
            {
                "code_execution_docker_image": "python:3.11-slim",
                "code_execution_memory_mb": 256,
            },
        )()

        _execute_docker("print('ok')", settings, timeout=30)

        cmd = mock_run.call_args[0][0]
        assert "--network" in cmd and "none" in cmd
        assert "--read-only" in cmd
        assert "--cap-drop" in cmd and "ALL" in cmd
        assert "--security-opt" in cmd and "no-new-privileges" in cmd
        assert "--user" in cmd and "65534:65534" in cmd


class TestExecuteNativeSandboxSecurity:
    """_execute_native_sandbox dispatches per OS correctly."""

    @patch("agentnexus.tools.code_executor._SYSTEM", "Haiku")
    def test_native_os_not_supported(self):
        """Raises SandboxUnavailable for unsupported OS."""
        with pytest.raises(SandboxUnavailable, match="unsupported OS"):
            _execute_native_sandbox("print(1)", 30)

    @patch("agentnexus.tools.code_executor._SYSTEM", "Linux")
    @patch("agentnexus.tools.code_executor._execute_bubblewrap")
    def test_native_linux_dispatches_to_bubblewrap(self, mock_bwrap):
        """On Linux dispatches to _execute_bubblewrap."""
        mock_bwrap.return_value = "bwrap ok"
        result = _execute_native_sandbox("print(1)", 30)
        assert mock_bwrap.called
        assert "bwrap" in result

    @patch("agentnexus.tools.code_executor._SYSTEM", "Darwin")
    @patch("agentnexus.tools.code_executor._execute_seatbelt")
    def test_native_darwin_dispatches_to_seatbelt(self, mock_seatbelt):
        """On Darwin dispatches to _execute_seatbelt."""
        mock_seatbelt.return_value = "seatbelt ok"
        result = _execute_native_sandbox("print(1)", 30)
        assert mock_seatbelt.called
        assert "seatbelt" in result

    @patch("agentnexus.tools.code_executor._SYSTEM", "Windows")
    @patch("agentnexus.tools.code_executor._execute_windows_native")
    def test_native_windows_dispatches_to_windows_native(self, mock_win):
        """On Windows dispatches to _execute_windows_native."""
        mock_win.side_effect = SandboxUnavailable("Windows native not available")
        with pytest.raises(SandboxUnavailable, match="Windows native"):
            _execute_native_sandbox("print(1)", 30)
        assert mock_win.called
