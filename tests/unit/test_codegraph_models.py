"""Unit tests for codegraph.models module."""

from __future__ import annotations

import pytest

from agentnexus.codegraph.models import (
    EMBEDDING_MAX_CHARS,
    EdgeData,
    EdgeKind,
    NodeData,
    NodeKind,
    ParseResult,
    build_embedding_text,
    make_module_qualname,
    make_node_id,
)


class TestNodeKind:
    def test_enum_values(self):
        assert NodeKind.FILE == "file"
        assert NodeKind.CLASS == "class"
        assert NodeKind.FUNCTION == "function"
        assert NodeKind.METHOD == "method"
        assert NodeKind.VARIABLE == "variable"
        assert NodeKind.IMPORT == "import"

    def test_enum_members(self):
        assert len(NodeKind) == 6


class TestEdgeKind:
    def test_enum_values(self):
        assert EdgeKind.CONTAINS == "contains"
        assert EdgeKind.INHERITS == "inherits"
        assert EdgeKind.CALLS == "calls"
        assert EdgeKind.IMPORTS == "imports"
        assert EdgeKind.USES == "uses"
        assert EdgeKind.DECORATES == "decorates"

    def test_enum_members(self):
        assert len(EdgeKind) == 6


class TestNodeData:
    def test_defaults(self):
        node = NodeData(
            id="test:id",
            kind=NodeKind.FUNCTION,
            name="test",
            qualified_name="pkg.test",
            file_path="test.py",
            language="python",
            start_line=1,
            end_line=10,
        )
        assert node.start_column == 0
        assert node.end_column == 0
        assert node.docstring is None
        assert node.signature is None
        assert node.visibility is None
        assert node.is_exported is False
        assert node.is_async is False
        assert node.is_static is False
        assert node.is_abstract is False
        assert node.decorators == []
        assert node.type_parameters == []

    def test_full_construction(self):
        node = NodeData(
            id="method:pkg.MyClass.my_method",
            kind=NodeKind.METHOD,
            name="my_method",
            qualified_name="pkg.MyClass.my_method",
            file_path="pkg/my_module.py",
            language="python",
            start_line=10,
            end_line=20,
            start_column=4,
            end_column=30,
            docstring="A test method.",
            signature="(self, x: int) -> str",
            visibility="public",
            is_async=True,
            is_static=False,
            is_abstract=True,
            decorators=["abstractmethod"],
        )
        assert node.is_async is True
        assert node.is_abstract is True
        assert "abstractmethod" in node.decorators


class TestEdgeData:
    def test_construction(self):
        edge = EdgeData(
            source="file:test.py",
            target="function:pkg.test",
            kind=EdgeKind.CONTAINS,
            line=1,
        )
        assert edge.source == "file:test.py"
        assert edge.target == "function:pkg.test"
        assert edge.kind == EdgeKind.CONTAINS
        assert edge.metadata is None
        assert edge.col is None


class TestParseResult:
    def test_defaults(self):
        result = ParseResult()
        assert result.nodes == []
        assert result.edges == []
        assert result.errors == []
        assert result.partial is False

    def test_with_data(self):
        node = NodeData(
            id="test:id",
            kind=NodeKind.FUNCTION,
            name="test",
            qualified_name="test",
            file_path="test.py",
            language="python",
            start_line=1,
            end_line=1,
        )
        result = ParseResult(
            nodes=[node],
            edges=[],
            errors=["error1"],
            partial=True,
        )
        assert len(result.nodes) == 1
        assert result.partial is True


class TestMakeNodeId:
    def test_file_node(self):
        assert make_node_id(NodeKind.FILE, "", "test.py") == "file:test.py"

    def test_function_node(self):
        assert make_node_id(NodeKind.FUNCTION, "pkg.func") == "function:pkg.func"

    def test_class_node(self):
        assert make_node_id(NodeKind.CLASS, "pkg.MyClass") == "class:pkg.MyClass"

    def test_method_node(self):
        assert make_node_id(NodeKind.METHOD, "pkg.MyClass.method") == "method:pkg.MyClass.method"


class TestMakeModuleQualname:
    def test_simple_file(self):
        assert make_module_qualname("test.py") == "test"

    def test_nested_file(self):
        assert make_module_qualname("agentnexus/tools/file_ops.py") == "agentnexus.tools.file_ops"

    def test_init_file(self):
        assert make_module_qualname("agentnexus/__init__.py") == "agentnexus"

    def test_with_project_root(self):
        assert make_module_qualname("agentnexus/tools/file_ops.py", "agentnexus") == "tools.file_ops"

    def test_backslash_path(self):
        assert make_module_qualname("agentnexus\\tools\\file_ops.py") == "agentnexus.tools.file_ops"


class TestBuildEmbeddingText:
    def test_function_with_docstring(self):
        node = NodeData(
            id="function:pkg.func",
            kind=NodeKind.FUNCTION,
            name="func",
            qualified_name="pkg.func",
            file_path="test.py",
            language="python",
            start_line=1,
            end_line=10,
            signature="(x: int) -> str",
            docstring="Convert integer to string.",
        )
        text = build_embedding_text(node)
        assert "func" in text
        assert "pkg" in text
        assert "(x: int) -> str" in text
        assert "Convert integer to string." in text

    def test_class_with_bases(self):
        node = NodeData(
            id="class:pkg.MyClass",
            kind=NodeKind.CLASS,
            name="MyClass",
            qualified_name="pkg.MyClass",
            file_path="test.py",
            language="python",
            start_line=1,
            end_line=20,
            decorators=["dataclass"],
            docstring="A test class.",
        )
        text = build_embedding_text(node)
        assert "MyClass" in text
        assert "A test class." in text

    def test_variable_returns_empty(self):
        node = NodeData(
            id="variable:pkg.MY_VAR",
            kind=NodeKind.VARIABLE,
            name="MY_VAR",
            qualified_name="pkg.MY_VAR",
            file_path="test.py",
            language="python",
            start_line=1,
            end_line=1,
        )
        assert build_embedding_text(node) == ""

    def test_import_returns_empty(self):
        node = NodeData(
            id="import:pkg.os",
            kind=NodeKind.IMPORT,
            name="os",
            qualified_name="pkg.os",
            file_path="test.py",
            language="python",
            start_line=1,
            end_line=1,
        )
        assert build_embedding_text(node) == ""

    def test_truncation(self):
        long_doc = "x" * 5000
        node = NodeData(
            id="function:pkg.func",
            kind=NodeKind.FUNCTION,
            name="func",
            qualified_name="pkg.func",
            file_path="test.py",
            language="python",
            start_line=1,
            end_line=10,
            docstring=long_doc,
        )
        text = build_embedding_text(node)
        assert len(text) <= EMBEDDING_MAX_CHARS


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
