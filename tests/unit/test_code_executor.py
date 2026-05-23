"""Tests for agentnexus.tools.code_executor."""

from unittest.mock import patch

from agentnexus.tools.code_executor import _ensure_main_block, python_execute


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
    def test_simple_execution(self, mock_settings):
        mock_settings.return_value.e2b_api_key.get_secret_value.return_value = ""
        result = python_execute("print('hello')")
        assert "hello" in result

    @patch("agentnexus.tools.code_executor.get_settings")
    def test_code_error(self, mock_settings):
        mock_settings.return_value.e2b_api_key.get_secret_value.return_value = ""
        result = python_execute("1/0")
        assert "ZeroDivisionError" in result or "error" in result.lower()
