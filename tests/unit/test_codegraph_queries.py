"""Unit tests for codegraph.queries module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from agentnexus.codegraph.models import EdgeData, EdgeKind, NodeData, NodeKind
from agentnexus.codegraph.queries import (
    SearchResult,
    _node_to_result,
    _resolve_symbol,
    codegraph_context,
    codegraph_relations,
    codegraph_search,
    get_callees,
    get_callers,
    get_entity_context,
    get_imports,
    get_inheritance_tree,
)
from agentnexus.codegraph.store import CodeGraphStore


@pytest.fixture
def sample_store(tmp_path):
    """Create a store with sample data."""
    db_path = tmp_path / "test.db"
    store = CodeGraphStore(db_path)
    store.init_schema()

    # Add file node
    file_node = NodeData(
        id="file:pkg/test.py",
        kind=NodeKind.FILE,
        name="test.py",
        qualified_name="pkg.test",
        file_path="pkg/test.py",
        language="python",
        start_line=1,
        end_line=100,
    )
    store.upsert_node(file_node)

    # Add function nodes
    func_a = NodeData(
        id="function:pkg.func_a",
        kind=NodeKind.FUNCTION,
        name="func_a",
        qualified_name="pkg.func_a",
        file_path="pkg/test.py",
        language="python",
        start_line=1,
        end_line=10,
        docstring="Function A",
        signature="(x: int) -> str",
    )
    func_b = NodeData(
        id="function:pkg.func_b",
        kind=NodeKind.FUNCTION,
        name="func_b",
        qualified_name="pkg.func_b",
        file_path="pkg/test.py",
        language="python",
        start_line=15,
        end_line=25,
        docstring="Function B",
    )
    store.upsert_node(func_a)
    store.upsert_node(func_b)

    # Add class node
    class_node = NodeData(
        id="class:pkg.MyClass",
        kind=NodeKind.CLASS,
        name="MyClass",
        qualified_name="pkg.MyClass",
        file_path="pkg/test.py",
        language="python",
        start_line=30,
        end_line=50,
        docstring="A test class",
    )
    store.upsert_node(class_node)

    # Add method node
    method_node = NodeData(
        id="method:pkg.MyClass.my_method",
        kind=NodeKind.METHOD,
        name="my_method",
        qualified_name="pkg.MyClass.my_method",
        file_path="pkg/test.py",
        language="python",
        start_line=35,
        end_line=40,
    )
    store.upsert_node(method_node)

    # Add edges
    store.upsert_edges_batch([
        EdgeData(source="file:pkg/test.py", target="function:pkg.func_a", kind=EdgeKind.CONTAINS),
        EdgeData(source="file:pkg/test.py", target="function:pkg.func_b", kind=EdgeKind.CONTAINS),
        EdgeData(source="function:pkg.func_a", target="function:pkg.func_b", kind=EdgeKind.CALLS, line=5),
        EdgeData(source="file:pkg/test.py", target="class:pkg.MyClass", kind=EdgeKind.CONTAINS),
        EdgeData(source="class:pkg.MyClass", target="method:pkg.MyClass.my_method", kind=EdgeKind.CONTAINS),
    ])

    # Add file tracking
    store.upsert_file(
        path="pkg/test.py",
        content_hash="abc123",
        language="python",
        size=1000,
        modified_at=1000,
        node_count=4,
    )

    yield store
    store.close()


class TestNodeToResult:
    def test_basic_conversion(self):
        node = {
            "id": "function:pkg.func",
            "kind": "function",
            "name": "func",
            "qualified_name": "pkg.func",
            "file_path": "test.py",
            "start_line": 1,
            "end_line": 10,
            "docstring": "Test doc",
            "signature": "(x: int) -> str",
            "visibility": "public",
            "is_async": 0,
            "decorators": None,
        }
        result = _node_to_result(node, score=0.9)
        assert result.id == "function:pkg.func"
        assert result.name == "func"
        assert result.score == 0.9
        assert result.docstring == "Test doc"

    def test_default_score(self):
        node = {
            "id": "function:pkg.func",
            "kind": "function",
            "name": "func",
            "qualified_name": "pkg.func",
            "file_path": "test.py",
            "start_line": 1,
            "end_line": 10,
        }
        result = _node_to_result(node)
        assert result.score == 1.0


class TestResolveSymbol:
    def test_resolve_by_exact_id(self, sample_store):
        result = _resolve_symbol(sample_store, "pkg.func_a")
        assert result == "function:pkg.func_a"

    def test_resolve_by_name(self, sample_store):
        result = _resolve_symbol(sample_store, "func_a")
        assert result == "function:pkg.func_a"

    def test_resolve_class(self, sample_store):
        result = _resolve_symbol(sample_store, "MyClass", kind="class")
        assert result == "class:pkg.MyClass"

    def test_resolve_nonexistent(self, sample_store):
        result = _resolve_symbol(sample_store, "nonexistent_func")
        assert result is None

    def test_resolve_with_like_match(self, sample_store):
        # LIKE match requires the qualified_name to contain the pattern
        result = _resolve_symbol(sample_store, "MyClass")
        assert result == "class:pkg.MyClass"


class TestGetCallers:
    def test_get_callers(self, sample_store):
        with patch("agentnexus.codegraph.queries._get_store", return_value=(sample_store, Path("."))):
            results = get_callers("func_b")
            assert len(results) >= 1
            assert any(r.name == "func_a" for r in results)

    def test_get_callers_nonexistent(self, sample_store):
        with patch("agentnexus.codegraph.queries._get_store", return_value=(sample_store, Path("."))):
            results = get_callers("nonexistent")
            assert results == []


class TestGetCallees:
    def test_get_callees(self, sample_store):
        with patch("agentnexus.codegraph.queries._get_store", return_value=(sample_store, Path("."))):
            results = get_callees("func_a")
            assert len(results) >= 1
            assert any(r.name == "func_b" for r in results)

    def test_get_callees_nonexistent(self, sample_store):
        with patch("agentnexus.codegraph.queries._get_store", return_value=(sample_store, Path("."))):
            results = get_callees("nonexistent")
            assert results == []


class TestGetInheritanceTree:
    def test_no_inheritance(self, sample_store):
        with patch("agentnexus.codegraph.queries._get_store", return_value=(sample_store, Path("."))):
            results = get_inheritance_tree("MyClass")
            assert results == []

    def test_nonexistent_class(self, sample_store):
        with patch("agentnexus.codegraph.queries._get_store", return_value=(sample_store, Path("."))):
            results = get_inheritance_tree("Nonexistent")
            assert results == []


class TestGetImports:
    def test_get_imports_empty(self, sample_store):
        with patch("agentnexus.codegraph.queries._get_store", return_value=(sample_store, Path("."))):
            results = get_imports("pkg/test.py")
            assert results == []


class TestGetEntityContext:
    def test_get_context(self, sample_store):
        with patch("agentnexus.codegraph.queries._get_store", return_value=(sample_store, Path("."))):
            ctx = get_entity_context("func_a")
            assert ctx is not None
            assert ctx["entity"].name == "func_a"
            assert isinstance(ctx["callers"], list)
            assert isinstance(ctx["callees"], list)

    def test_get_context_nonexistent(self, sample_store):
        with patch("agentnexus.codegraph.queries._get_store", return_value=(sample_store, Path("."))):
            ctx = get_entity_context("nonexistent")
            assert ctx is None


class TestAgentTools:
    def test_codegraph_search(self):
        with patch("agentnexus.codegraph.queries.search_entities", return_value=[
            SearchResult(
                id="function:pkg.func",
                kind="function",
                name="func",
                qualified_name="pkg.func",
                file_path="test.py",
                start_line=1,
                end_line=10,
                score=0.9,
                docstring="Test",
                signature="(x: int)",
            )
        ]):
            results = codegraph_search("test query")
            assert len(results) == 1
            assert results[0]["name"] == "func"
            assert results[0]["score"] == 0.9

    def test_codegraph_relations_callers(self):
        with patch("agentnexus.codegraph.queries.get_callers", return_value=[
            SearchResult(
                id="function:pkg.caller",
                kind="function",
                name="caller",
                qualified_name="pkg.caller",
                file_path="test.py",
                start_line=1,
                end_line=10,
                score=1.0,
            )
        ]):
            results = codegraph_relations("func", "callers")
            assert len(results) == 1
            assert results[0]["name"] == "caller"

    def test_codegraph_relations_callees(self):
        with patch("agentnexus.codegraph.queries.get_callees", return_value=[]):
            results = codegraph_relations("func", "callees")
            assert results == []

    def test_codegraph_relations_inherits(self):
        with patch("agentnexus.codegraph.queries.get_inheritance_tree", return_value=[]):
            results = codegraph_relations("MyClass", "inherits")
            assert results == []

    def test_codegraph_relations_imports(self):
        with patch("agentnexus.codegraph.queries.get_imports", return_value=[]):
            results = codegraph_relations("module", "imports")
            assert results == []

    def test_codegraph_relations_unknown(self):
        results = codegraph_relations("symbol", "unknown_type")
        assert len(results) == 1
        assert "error" in results[0]

    def test_codegraph_context_found(self):
        with patch("agentnexus.codegraph.queries.get_entity_context", return_value={
            "entity": SearchResult(
                id="function:pkg.func",
                kind="function",
                name="func",
                qualified_name="pkg.func",
                file_path="test.py",
                start_line=1,
                end_line=10,
                docstring="Test",
                signature="(x: int)",
                score=1.0,
            ),
            "callers": [],
            "callees": [],
        }):
            result = codegraph_context("func")
            assert result["entity"]["name"] == "func"

    def test_codegraph_context_not_found(self):
        with patch("agentnexus.codegraph.queries.get_entity_context", return_value=None):
            result = codegraph_context("nonexistent")
            assert "error" in result


class TestSearchResult:
    def test_defaults(self):
        result = SearchResult(
            id="test:id",
            kind="function",
            name="func",
            qualified_name="pkg.func",
            file_path="test.py",
            start_line=1,
            end_line=10,
            score=0.0,
        )
        assert result.score == 0.0
        assert result.docstring is None
        assert result.signature is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
