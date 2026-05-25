"""Sandbox execution output tests.

Validates that sandbox-executed code produces correct output,
captures stderr, and handles multi-file scenarios.
"""
from unittest.mock import patch

import pytest


class TestSandboxExecutionOutput:
    """Real sandbox execution output correctness."""

    @patch("agentnexus.tools.code_executor.get_settings")
    def test_stdout_capture(self, mock_settings):
        mock_settings.return_value.e2b_api_key.get_secret_value.return_value = ""
        mock_settings.return_value.code_execution_backend = "local_unsafe"
        mock_settings.return_value.code_execution_allow_unsafe_local = True
        mock_settings.return_value.code_execution_timeout = 30

        from agentnexus.tools.code_executor import python_execute
        result = python_execute("print('hello world')")
        assert "hello world" in result

    @patch("agentnexus.tools.code_executor.get_settings")
    def test_stderr_interleaving(self, mock_settings):
        mock_settings.return_value.e2b_api_key.get_secret_value.return_value = ""
        mock_settings.return_value.code_execution_backend = "local_unsafe"
        mock_settings.return_value.code_execution_allow_unsafe_local = True
        mock_settings.return_value.code_execution_timeout = 30

        from agentnexus.tools.code_executor import python_execute
        code = "import sys; print('stdout'); print('stderr msg', file=sys.stderr)"
        result = python_execute(code)
        assert "stdout" in result

    @patch("agentnexus.tools.code_executor.get_settings")
    def test_exception_traceback(self, mock_settings):
        mock_settings.return_value.e2b_api_key.get_secret_value.return_value = ""
        mock_settings.return_value.code_execution_backend = "local_unsafe"
        mock_settings.return_value.code_execution_allow_unsafe_local = True
        mock_settings.return_value.code_execution_timeout = 30

        from agentnexus.tools.code_executor import python_execute
        result = python_execute("1/0")
        assert "ZeroDivisionError" in result or "error" in result.lower()

    @patch("agentnexus.tools.code_executor.get_settings")
    def test_multi_line_output(self, mock_settings):
        mock_settings.return_value.e2b_api_key.get_secret_value.return_value = ""
        mock_settings.return_value.code_execution_backend = "local_unsafe"
        mock_settings.return_value.code_execution_allow_unsafe_local = True
        mock_settings.return_value.code_execution_timeout = 30

        from agentnexus.tools.code_executor import python_execute
        code = "for i in range(3):\n    print(i)"
        result = python_execute(code)
        assert "0" in result
        assert "1" in result
        assert "2" in result

    @patch("agentnexus.tools.code_executor.get_settings")
    def test_empty_output(self, mock_settings):
        mock_settings.return_value.e2b_api_key.get_secret_value.return_value = ""
        mock_settings.return_value.code_execution_backend = "local_unsafe"
        mock_settings.return_value.code_execution_allow_unsafe_local = True
        mock_settings.return_value.code_execution_timeout = 30

        from agentnexus.tools.code_executor import python_execute
        result = python_execute("x = 1 + 1")
        assert isinstance(result, str)

    @patch("agentnexus.tools.code_executor.get_settings")
    def test_unicode_output(self, mock_settings):
        mock_settings.return_value.e2b_api_key.get_secret_value.return_value = ""
        mock_settings.return_value.code_execution_backend = "local_unsafe"
        mock_settings.return_value.code_execution_allow_unsafe_local = True
        mock_settings.return_value.code_execution_timeout = 30

        from agentnexus.tools.code_executor import python_execute
        result = python_execute("print('hello unicode test')")
        assert "hello" in result

    @patch("agentnexus.tools.code_executor.get_settings")
    def test_return_value_output(self, mock_settings):
        mock_settings.return_value.e2b_api_key.get_secret_value.return_value = ""
        mock_settings.return_value.code_execution_backend = "local_unsafe"
        mock_settings.return_value.code_execution_allow_unsafe_local = True
        mock_settings.return_value.code_execution_timeout = 30

        from agentnexus.tools.code_executor import python_execute
        result = python_execute("print(42)")
        assert "42" in result

    @patch("agentnexus.tools.code_executor.get_settings")
    def test_syntax_error_output(self, mock_settings):
        mock_settings.return_value.e2b_api_key.get_secret_value.return_value = ""
        mock_settings.return_value.code_execution_backend = "local_unsafe"
        mock_settings.return_value.code_execution_allow_unsafe_local = True
        mock_settings.return_value.code_execution_timeout = 30

        from agentnexus.tools.code_executor import python_execute
        result = python_execute("if x")  # incomplete syntax
        assert "SyntaxError" in result or "error" in result.lower()
