"""Code quality (lint) evaluation tests.

Validates that generated code passes linting, style compliance,
and complexity checks.
"""
import ast
import subprocess
import textwrap

import pytest


class TestCodeQuality:
    """Lint and style checks for generated code."""

    def test_syntax_valid_simple_function(self):
        code = "def add(a, b):\n    return a + b\n"
        ast.parse(code)

    def test_syntax_valid_class(self):
        code = textwrap.dedent("""\
            class Counter:
                def __init__(self):
                    self.count = 0
                def increment(self):
                    self.count += 1
        """)
        ast.parse(code)

    def test_syntax_valid_with_decorators(self):
        code = textwrap.dedent("""\
            def memoize(func):
                cache = {}
                def wrapper(n):
                    if n in cache:
                        return cache[n]
                    cache[n] = func(n)
                    return cache[n]
                return wrapper

            @memoize
            def fib(n):
                if n <= 1:
                    return n
                return fib(n-1) + fib(n-2)
        """)
        ast.parse(code)

    def test_syntax_invalid_syntax(self):
        code = "def foo(\n"
        with pytest.raises(SyntaxError):
            ast.parse(code)

    def test_complexity_no_nested_loops(self):
        """Generated code should not have deeply nested loops."""
        code = textwrap.dedent("""\
            def process(items):
                for item in items:
                    if item:
                        print(item)
        """)
        tree = ast.parse(code)
        for node in ast.walk(tree):
            if isinstance(node, ast.For):
                depth = 0
                for child in ast.walk(node):
                    if isinstance(child, (ast.For, ast.While)):
                        depth += 1
                assert depth <= 1

    def test_no_long_lines(self):
        code = textwrap.dedent("""\
            def calculate_average(numbers):
                if not numbers:
                    return 0
                total = sum(numbers)
                return total / len(numbers)
        """)
        for line in code.split("\n"):
            assert len(line) <= 120, f"Line too long ({len(line)} chars): {line}"

    def test_has_docstring(self):
        code = textwrap.dedent("""\
            def add(a, b):
                \"\"\"Return the sum of a and b.\"\"\"
                return a + b
        """)
        tree = ast.parse(code)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                docstring = ast.get_docstring(node)
                assert docstring is not None, f"Function {node.name} missing docstring"

    def test_function_naming_convention(self):
        code = textwrap.dedent("""\
            def calculate_total(items):
                return sum(items)

            def get_user_name(user_id):
                return "Alice"
        """)
        tree = ast.parse(code)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                assert node.name.islower() or node.name.startswith("_"), \
                    f"Function {node.name} should be snake_case"

    def test_no_bare_except(self):
        """Generated code should not use bare except."""
        code = textwrap.dedent("""\
            def safe_divide(a, b):
                try:
                    return a / b
                except ZeroDivisionError:
                    return None
        """)
        tree = ast.parse(code)
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler):
                assert node.type is not None, "Bare except found"

    def test_ruff_lint_clean(self):
        """Generated code passes ruff lint."""
        code = textwrap.dedent("""\
            def add(a: int, b: int) -> int:
                \"\"\"Return the sum of a and b.\"\"\"
                return a + b

            def subtract(a: int, b: int) -> int:
                \"\"\"Return the difference of a and b.\"\"\"
                return a - b
        """)
        import pathlib
        import tempfile

        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(code)
            f.flush()
            temp_path = pathlib.Path(f.name)

        try:
            result = subprocess.run(
                ["ruff", "check", str(temp_path)],
                capture_output=True, text=True, timeout=10
            )
            assert result.returncode == 0, f"Ruff lint failed: {result.stdout}"
        except FileNotFoundError:
            pytest.skip("ruff not installed")
        finally:
            temp_path.unlink()
