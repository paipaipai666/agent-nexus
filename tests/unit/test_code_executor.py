"""Tests for agentnexus.tools.code_executor."""

from unittest.mock import patch

from agentnexus.tools.code_executor import (
    SandboxUnavailable,
    _ensure_main_block,
    _execute_docker,
    _execute_windows_native,
    python_execute,
)


class TestEnsureMainBlock:
    def test_keeps_existing_main(self):
        code = 'def foo():\n    pass\n\nif __name__ == "__main__":\n    foo()'
        result = _ensure_main_block(code)
        assert result == code

    def test_appends_for_function_without_args(self):
        code = "def hello():\n    print('hi')"
        result = _ensure_main_block(code)
        assert "Auto-appended" in result
        assert "hello()" in result

    def test_fallback_when_no_functions(self):
        code = "x = 1"
        result = _ensure_main_block(code)
        assert "Auto-executed" in result

    def test_syntax_error_fallback(self):
        code = "this is not valid python {{{"
        result = _ensure_main_block(code)
        assert "Auto-executed" in result


class TestPythonExecute:
    @patch("agentnexus.tools.code_executor.get_settings")
    def test_local_unsafe_requires_explicit_opt_in(self, mock_settings):
        mock_settings.return_value.e2b_api_key.get_secret_value.return_value = ""
        mock_settings.return_value.code_execution_backend = "local_unsafe"
        mock_settings.return_value.code_execution_allow_unsafe_local = False
        result = python_execute("print('hello')")
        assert "requires" in result
        assert "local_unsafe" in result

    @patch("agentnexus.tools.code_executor.get_settings")
    def test_local_unsafe_simple_execution(self, mock_settings):
        mock_settings.return_value.e2b_api_key.get_secret_value.return_value = ""
        mock_settings.return_value.code_execution_backend = "local_unsafe"
        mock_settings.return_value.code_execution_allow_unsafe_local = True
        mock_settings.return_value.code_execution_timeout = 30
        result = python_execute("print('hello')")
        assert "hello" in result

    @patch("agentnexus.tools.code_executor.get_settings")
    def test_code_error(self, mock_settings):
        mock_settings.return_value.e2b_api_key.get_secret_value.return_value = ""
        mock_settings.return_value.code_execution_backend = "local_unsafe"
        mock_settings.return_value.code_execution_allow_unsafe_local = True
        mock_settings.return_value.code_execution_timeout = 30
        result = python_execute("1/0")
        assert "ZeroDivisionError" in result or "error" in result.lower()

    @patch("agentnexus.tools.code_executor._execute_docker")
    @patch("agentnexus.tools.code_executor._execute_native_sandbox")
    @patch("agentnexus.tools.code_executor._execute_e2b")
    @patch("agentnexus.tools.code_executor.get_settings")
    def test_auto_fallback_order(self, mock_settings, mock_e2b, mock_native, mock_docker):
        mock_settings.return_value.e2b_api_key.get_secret_value.return_value = "sk-test"
        mock_settings.return_value.code_execution_backend = "auto"
        mock_settings.return_value.code_execution_timeout = 30
        mock_e2b.side_effect = Exception("e2b down")
        mock_native.side_effect = Exception("native down")
        mock_docker.return_value = "docker ok"

        result = python_execute("print('hello')")

        assert result == "docker ok"
        assert mock_e2b.called
        assert mock_native.called
        assert mock_docker.called

    @patch("agentnexus.tools.code_executor.shutil.which")
    @patch("agentnexus.tools.code_executor.get_settings")
    def test_auto_warns_and_runs_local_when_no_safe_backend(self, mock_settings, mock_which):
        mock_settings.return_value.e2b_api_key.get_secret_value.return_value = ""
        mock_settings.return_value.code_execution_backend = "auto"
        mock_settings.return_value.code_execution_timeout = 30
        mock_which.return_value = None

        result = python_execute("print('hello')")

        assert "[warning]" in result
        assert "unsafe local subprocess" in result
        assert "hello" in result

    @patch("agentnexus.tools.code_executor.subprocess.run")
    @patch("agentnexus.tools.code_executor.shutil.which")
    def test_docker_backend_uses_restricted_container_flags(self, mock_which, mock_run):
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

        result = _execute_docker("print('ok')", settings, timeout=30)

        cmd = mock_run.call_args[0][0]
        assert "ok" in result
        assert "--network" in cmd and "none" in cmd
        assert "--read-only" in cmd
        assert "--cap-drop" in cmd and "ALL" in cmd
        assert "--security-opt" in cmd and "no-new-privileges" in cmd

    def test_windows_native_backend_reports_unavailable(self):
        try:
            _execute_windows_native("print(1)", timeout=30)
        except SandboxUnavailable as exc:
            assert "Windows native sandbox runner" in str(exc)
        else:
            raise AssertionError("Windows native backend should not run without a launcher")
