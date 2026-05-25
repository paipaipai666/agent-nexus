"""Performance benchmarks for shell execution backend dispatching."""

from __future__ import annotations

from agentnexus.tools.shell import (
    ShellSandboxUnavailable,
    _execute_shell_auto,
    _execute_shell_bubblewrap,
    _execute_shell_docker,
    shell_exec,
)


class TestShellAutoFallbackChain:
    """Benchmark the fallback chain through all shell backends."""

    def test_auto_fallback_e2b_to_native(self, mocker, benchmark):
        """Benchmark: e2b unavailable -> native sandbox fallback."""
        mocker.patch("agentnexus.tools.shell._execute_shell_e2b",
                     side_effect=ShellSandboxUnavailable("e2b"))
        mocker.patch("agentnexus.tools.shell._execute_shell_native",
                      return_value="ok")

        result = benchmark(_execute_shell_auto, "ls", "/tmp", mocker.MagicMock(), 30)
        assert result is not None

    def test_auto_e2b_success_latency(self, mocker, benchmark):
        """Benchmark: e2b success path (fast path, no fallback)."""
        mocker.patch("agentnexus.tools.shell._execute_shell_e2b",
                     return_value="e2b quick result")

        result = benchmark(_execute_shell_auto, "ls", "/tmp", mocker.MagicMock(), 30)
        assert "e2b" in result

    def test_auto_all_backends_fail(self, mocker, benchmark):
        """Benchmark: complete fallback chain (worst case)."""
        mocker.patch("agentnexus.tools.shell._execute_shell_e2b",
                     side_effect=ShellSandboxUnavailable("e2b"))
        mocker.patch("agentnexus.tools.shell._execute_shell_native",
                     side_effect=ShellSandboxUnavailable("native"))
        mocker.patch("agentnexus.tools.shell._execute_shell_docker",
                     side_effect=ShellSandboxUnavailable("docker"))
        mocker.patch("agentnexus.tools.shell._execute_shell_locally_with_warning",
                     return_value="[warning]\nfallback")

        result = benchmark(_execute_shell_auto, "ls", "/tmp", mocker.MagicMock(), 30)
        assert "warning" in result


class TestShellExecBackendDispatch:
    """Benchmark shell_exec with different backends."""

    def test_shell_exec_auto(self, mocker, benchmark):
        """Benchmark shell_exec with auto backend."""
        settings = mocker.MagicMock()
        settings.shell_enabled = True
        settings.shell_execution_backend = "auto"
        settings.shell_timeout = 30
        mocker.patch("agentnexus.tools.shell.get_settings", return_value=settings)
        mocker.patch("agentnexus.tools.shell._check_blacklist", return_value=None)
        mocker.patch("agentnexus.tools.file_ops._resolve_safe")
        mocker.patch("agentnexus.tools.shell._execute_shell_auto", return_value="ok")

        result = benchmark(shell_exec, "ls")
        assert result is not None

    def test_shell_exec_disabled(self, mocker, benchmark):
        """Benchmark shell_exec with disabled backend (fastest path)."""
        settings = mocker.MagicMock()
        settings.shell_enabled = True
        settings.shell_execution_backend = "disabled"
        mocker.patch("agentnexus.tools.shell.get_settings", return_value=settings)
        mocker.patch("agentnexus.tools.shell._check_blacklist", return_value=None)

        result = benchmark(shell_exec, "ls")
        assert "[blocked]" in result

    def test_shell_exec_sandbox_unavailable(self, mocker, benchmark):
        """Benchmark: sandbox unavailable error path."""
        settings = mocker.MagicMock()
        settings.shell_enabled = True
        settings.shell_execution_backend = "native"
        mocker.patch("agentnexus.tools.shell.get_settings", return_value=settings)
        mocker.patch("agentnexus.tools.shell._check_blacklist", return_value=None)
        mocker.patch("agentnexus.tools.file_ops._resolve_safe")
        mocker.patch("agentnexus.tools.shell._execute_shell_native",
                     side_effect=ShellSandboxUnavailable("not installed"))

        result = benchmark(shell_exec, "ls")
        assert "sandbox" in result or "blocked" in result or "错误" in result


class TestShellBackendConstruction:
    """Benchmark sandbox backend command construction without execution."""

    def test_docker_cmd_construction(self, mocker, benchmark):
        """Benchmark docker command list construction."""
        mocker.patch("shutil.which", return_value="/usr/bin/docker")
        mocker.patch("agentnexus.tools.shell._run_shell_command", return_value="ok")

        result = benchmark(_execute_shell_docker, "ls", "/tmp", mocker.MagicMock(), 30)
        assert "ok" in result

    def test_bubblewrap_cmd_construction(self, mocker, benchmark):
        """Benchmark bubblewrap command list construction."""
        mocker.patch("shutil.which", return_value="/usr/bin/bwrap")
        mocker.patch("agentnexus.tools.shell._run_shell_command", return_value="ok")

        result = benchmark(_execute_shell_bubblewrap, "ls", "/tmp", 30)
        assert "ok" in result
