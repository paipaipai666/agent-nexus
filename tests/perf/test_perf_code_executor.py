"""Performance benchmarks for code execution backend dispatching."""

from __future__ import annotations

from agentnexus.tools.code_executor import (
    SandboxUnavailable,
    _execute_auto,
    python_execute,
)


class TestCodeExecAutoFallbackChain:
    """Benchmark the auto fallback chain for code execution."""

    def test_auto_e2b_success(self, mocker, benchmark):
        """Benchmark: e2b success path (fastest)."""
        mocker.patch("agentnexus.tools.code_executor._has_e2b_key",
                     return_value=True)
        mocker.patch("agentnexus.tools.code_executor._execute_e2b",
                     return_value="e2b ok")

        result = benchmark(_execute_auto, "print(1)", mocker.MagicMock(), 30)
        assert "e2b" in result

    def test_auto_all_fallthrough(self, mocker, benchmark):
        """Benchmark: all backends fail -> local fallback."""
        mocker.patch("agentnexus.tools.code_executor._has_e2b_key",
                     return_value=True)
        mocker.patch("agentnexus.tools.code_executor._execute_e2b",
                     side_effect=SandboxUnavailable("e2b"))
        mocker.patch("agentnexus.tools.code_executor._execute_native_sandbox",
                     side_effect=SandboxUnavailable("native"))
        mocker.patch("agentnexus.tools.code_executor._execute_docker",
                     side_effect=SandboxUnavailable("docker"))
        mocker.patch("agentnexus.tools.code_executor._execute_locally_with_warning",
                     return_value="[warning]\nfallback")

        result = benchmark(_execute_auto, "print(1)", mocker.MagicMock(), 30)
        assert "warning" in result


class TestPythonExecBackendDispatch:
    """Benchmark python_execute with different backends."""

    def test_disabled_backend(self, mocker, benchmark):
        """Benchmark: disabled backend (fastest)."""
        settings = mocker.MagicMock()
        settings.code_execution_backend = "disabled"
        mocker.patch("agentnexus.tools.code_executor.get_settings",
                     return_value=settings)

        result = benchmark(python_execute, "print(1)")
        assert "[blocked]" in result

    def test_local_unsafe_blocked(self, mocker, benchmark):
        """Benchmark: local_unsafe blocked without opt-in."""
        settings = mocker.MagicMock()
        settings.code_execution_backend = "local_unsafe"
        settings.code_execution_allow_unsafe_local = False
        mocker.patch("agentnexus.tools.code_executor.get_settings",
                     return_value=settings)

        result = benchmark(python_execute, "print(1)")
        assert "[blocked]" in result
