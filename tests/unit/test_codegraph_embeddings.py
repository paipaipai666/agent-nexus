"""Unit tests for codegraph.embeddings module."""

from __future__ import annotations

import pytest

from agentnexus.codegraph.embeddings import generate_embeddings_batch
from agentnexus.codegraph.models import NodeData, NodeKind


class TestGenerateEmbeddingsBatch:
    def test_function_node_gets_embedding(self):
        nodes = [
            NodeData(
                id="function:test.func",
                kind=NodeKind.FUNCTION,
                name="func",
                qualified_name="test.func",
                file_path="test.py",
                language="python",
                start_line=1,
                end_line=10,
                signature="(x: int) -> str",
                docstring="A test function.",
            )
        ]
        embeddings = generate_embeddings_batch(nodes)
        assert len(embeddings) == 1
        assert embeddings[0] is not None
        assert len(embeddings[0]) > 0

    def test_class_node_gets_embedding(self):
        nodes = [
            NodeData(
                id="class:test.MyClass",
                kind=NodeKind.CLASS,
                name="MyClass",
                qualified_name="test.MyClass",
                file_path="test.py",
                language="python",
                start_line=1,
                end_line=20,
                docstring="A test class.",
            )
        ]
        embeddings = generate_embeddings_batch(nodes)
        assert len(embeddings) == 1
        assert embeddings[0] is not None

    def test_variable_node_no_embedding(self):
        nodes = [
            NodeData(
                id="variable:test.MY_VAR",
                kind=NodeKind.VARIABLE,
                name="MY_VAR",
                qualified_name="test.MY_VAR",
                file_path="test.py",
                language="python",
                start_line=1,
                end_line=1,
            )
        ]
        embeddings = generate_embeddings_batch(nodes)
        assert len(embeddings) == 1
        assert embeddings[0] is None

    def test_import_node_no_embedding(self):
        nodes = [
            NodeData(
                id="import:test.os",
                kind=NodeKind.IMPORT,
                name="os",
                qualified_name="test.os",
                file_path="test.py",
                language="python",
                start_line=1,
                end_line=1,
            )
        ]
        embeddings = generate_embeddings_batch(nodes)
        assert len(embeddings) == 1
        assert embeddings[0] is None

    def test_mixed_nodes(self):
        nodes = [
            NodeData(
                id="function:test.func",
                kind=NodeKind.FUNCTION,
                name="func",
                qualified_name="test.func",
                file_path="test.py",
                language="python",
                start_line=1,
                end_line=10,
            ),
            NodeData(
                id="variable:test.MY_VAR",
                kind=NodeKind.VARIABLE,
                name="MY_VAR",
                qualified_name="test.MY_VAR",
                file_path="test.py",
                language="python",
                start_line=11,
                end_line=11,
            ),
            NodeData(
                id="class:test.MyClass",
                kind=NodeKind.CLASS,
                name="MyClass",
                qualified_name="test.MyClass",
                file_path="test.py",
                language="python",
                start_line=12,
                end_line=30,
            ),
        ]
        embeddings = generate_embeddings_batch(nodes)
        assert len(embeddings) == 3
        assert embeddings[0] is not None  # function
        assert embeddings[1] is None  # variable
        assert embeddings[2] is not None  # class

    def test_empty_list(self):
        embeddings = generate_embeddings_batch([])
        assert embeddings == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
