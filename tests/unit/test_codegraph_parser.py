"""Unit tests for codegraph.parsers.python_parser module."""

from __future__ import annotations

import pytest

from agentnexus.codegraph.models import EdgeKind, NodeKind
from agentnexus.codegraph.parsers.python_parser import PythonParser


@pytest.fixture
def parser():
    return PythonParser()


class TestPythonParser:
    def test_language(self, parser):
        assert parser.language == "python"

    def test_file_extensions(self, parser):
        assert ".py" in parser.file_extensions


class TestParseFunctions:
    def test_simple_function(self, parser, tmp_path):
        content = '''
def hello():
    """Say hello"""
    print("hello")
'''
        result = parser.parse_file(tmp_path / "test.py", content)
        assert not result.partial
        assert len(result.errors) == 0

        func_nodes = [n for n in result.nodes if n.kind == NodeKind.FUNCTION]
        assert len(func_nodes) == 1
        assert func_nodes[0].name == "hello"
        assert func_nodes[0].docstring == "Say hello"

    def test_function_with_args(self, parser, tmp_path):
        content = '''
def add(x: int, y: int = 0) -> int:
    return x + y
'''
        result = parser.parse_file(tmp_path / "test.py", content)
        func_nodes = [n for n in result.nodes if n.kind == NodeKind.FUNCTION]
        assert len(func_nodes) == 1
        assert func_nodes[0].name == "add"
        assert "x: int" in func_nodes[0].signature
        assert "y: int" in func_nodes[0].signature
        assert "-> int" in func_nodes[0].signature

    def test_async_function(self, parser, tmp_path):
        content = '''
async def fetch_data(url: str) -> dict:
    """Fetch data from URL."""
    return {}
'''
        result = parser.parse_file(tmp_path / "test.py", content)
        func_nodes = [n for n in result.nodes if n.kind == NodeKind.FUNCTION]
        assert len(func_nodes) == 1
        assert func_nodes[0].is_async is True

    def test_function_with_decorators(self, parser, tmp_path):
        content = '''
@app.command()
def my_command():
    pass
'''
        result = parser.parse_file(tmp_path / "test.py", content)
        func_nodes = [n for n in result.nodes if n.kind == NodeKind.FUNCTION]
        assert len(func_nodes) == 1
        # Decorator should contain some representation of app.command()
        assert len(func_nodes[0].decorators) > 0


class TestParseClasses:
    def test_simple_class(self, parser, tmp_path):
        content = '''
class MyClass:
    """A simple class."""
    pass
'''
        result = parser.parse_file(tmp_path / "test.py", content)
        class_nodes = [n for n in result.nodes if n.kind == NodeKind.CLASS]
        assert len(class_nodes) == 1
        assert class_nodes[0].name == "MyClass"
        assert class_nodes[0].docstring == "A simple class."

    def test_class_with_inheritance(self, parser, tmp_path):
        content = '''
class Child(Parent):
    pass
'''
        result = parser.parse_file(tmp_path / "test.py", content)
        class_nodes = [n for n in result.nodes if n.kind == NodeKind.CLASS]
        assert len(class_nodes) == 1
        assert class_nodes[0].name == "Child"

        inherit_edges = [e for e in result.edges if e.kind == EdgeKind.INHERITS]
        assert len(inherit_edges) == 1

    def test_class_with_methods(self, parser, tmp_path):
        content = '''
class MyClass:
    def method1(self):
        pass

    def method2(self, x: int) -> str:
        return str(x)
'''
        result = parser.parse_file(tmp_path / "test.py", content)
        class_nodes = [n for n in result.nodes if n.kind == NodeKind.CLASS]
        method_nodes = [n for n in result.nodes if n.kind == NodeKind.METHOD]
        assert len(class_nodes) == 1
        assert len(method_nodes) == 2

    def test_static_method(self, parser, tmp_path):
        content = '''
class MyClass:
    @staticmethod
    def static_method():
        pass
'''
        result = parser.parse_file(tmp_path / "test.py", content)
        method_nodes = [n for n in result.nodes if n.kind == NodeKind.METHOD]
        assert len(method_nodes) == 1
        assert method_nodes[0].is_static is True

    def test_abstract_method(self, parser, tmp_path):
        content = '''
from abc import abstractmethod

class MyClass:
    @abstractmethod
    def abstract_method(self):
        pass
'''
        result = parser.parse_file(tmp_path / "test.py", content)
        method_nodes = [n for n in result.nodes if n.kind == NodeKind.METHOD]
        assert len(method_nodes) == 1
        assert method_nodes[0].is_abstract is True


class TestParseImports:
    def test_import_statement(self, parser, tmp_path):
        content = '''
import os
import sys
'''
        result = parser.parse_file(tmp_path / "test.py", content)
        import_nodes = [n for n in result.nodes if n.kind == NodeKind.IMPORT]
        assert len(import_nodes) == 2

        import_edges = [e for e in result.edges if e.kind == EdgeKind.IMPORTS]
        assert len(import_edges) == 2

    def test_from_import(self, parser, tmp_path):
        content = '''
from pathlib import Path
from typing import List, Dict
'''
        result = parser.parse_file(tmp_path / "test.py", content)
        import_nodes = [n for n in result.nodes if n.kind == NodeKind.IMPORT]
        assert len(import_nodes) == 3  # Path, List, Dict


class TestParseSyntaxError:
    def test_syntax_error_returns_partial(self, parser, tmp_path):
        content = '''
def broken(
    this is not valid python
'''
        result = parser.parse_file(tmp_path / "test.py", content)
        assert result.partial is True
        assert len(result.errors) > 0
        assert any("SyntaxError" in e for e in result.errors)


class TestContainsEdges:
    def test_file_contains_function(self, parser, tmp_path):
        content = '''
def hello():
    pass
'''
        result = parser.parse_file(tmp_path / "test.py", content)
        contains_edges = [e for e in result.edges if e.kind == EdgeKind.CONTAINS]
        # At least one contains edge: file -> function
        assert len(contains_edges) >= 1

    def test_file_contains_class(self, parser, tmp_path):
        content = '''
class MyClass:
    pass
'''
        result = parser.parse_file(tmp_path / "test.py", content)
        contains_edges = [e for e in result.edges if e.kind == EdgeKind.CONTAINS]
        assert len(contains_edges) >= 1


class TestVisibility:
    def test_public_function(self, parser, tmp_path):
        content = '''
def public_func():
    pass
'''
        result = parser.parse_file(tmp_path / "test.py", content)
        func_nodes = [n for n in result.nodes if n.kind == NodeKind.FUNCTION]
        assert func_nodes[0].visibility == "public"

    def test_private_function(self, parser, tmp_path):
        content = '''
def _private_func():
    pass
'''
        result = parser.parse_file(tmp_path / "test.py", content)
        func_nodes = [n for n in result.nodes if n.kind == NodeKind.FUNCTION]
        assert func_nodes[0].visibility == "private"

    def test_dunder_method_is_public(self, parser, tmp_path):
        content = '''
class MyClass:
    def __init__(self):
        pass
'''
        result = parser.parse_file(tmp_path / "test.py", content)
        method_nodes = [n for n in result.nodes if n.kind == NodeKind.METHOD]
        init_method = next(m for m in method_nodes if m.name == "__init__")
        assert init_method.visibility == "public"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
