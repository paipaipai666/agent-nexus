"""Unit tests for codegraph.vector_store module."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agentnexus.codegraph.models import NodeData, NodeKind
from agentnexus.codegraph.vector_store import (
    COLLECTION_METADATA,
    COLLECTION_NAME,
    _get_codegraph_collection,
    clear_collection,
    delete_by_file,
    delete_by_ids,
    get_all_ids,
    search_semantic,
    upsert_nodes,
)


@pytest.fixture
def sample_nodes():
    """Sample nodes for testing."""
    return [
        NodeData(
            id="function:pkg.func_a",
            kind=NodeKind.FUNCTION,
            name="func_a",
            qualified_name="pkg.func_a",
            file_path="pkg/test.py",
            language="python",
            start_line=1,
            end_line=10,
        ),
        NodeData(
            id="class:pkg.MyClass",
            kind=NodeKind.CLASS,
            name="MyClass",
            qualified_name="pkg.MyClass",
            file_path="pkg/test.py",
            language="python",
            start_line=15,
            end_line=30,
        ),
    ]


@pytest.fixture
def sample_embeddings():
    """Sample embeddings for testing."""
    return [
        [0.1, 0.2, 0.3],
        [0.4, 0.5, 0.6],
    ]


class TestGetCodegraphCollection:
    @patch("agentnexus.codegraph.vector_store.get_collection")
    def test_creates_collection(self, mock_get_collection):
        mock_collection = MagicMock()
        mock_get_collection.return_value = mock_collection

        result = _get_codegraph_collection()

        mock_get_collection.assert_called_once_with(
            name=COLLECTION_NAME,
            metadata=COLLECTION_METADATA,
        )
        assert result == mock_collection


class TestUpsertNodes:
    @patch("agentnexus.codegraph.vector_store._get_codegraph_collection")
    def test_upsert_with_embeddings(self, mock_get_col, sample_nodes, sample_embeddings):
        mock_collection = MagicMock()
        mock_get_col.return_value = mock_collection

        count = upsert_nodes(sample_nodes, sample_embeddings, "hash123")

        assert count == 2
        mock_collection.upsert.assert_called_once()
        call_args = mock_collection.upsert.call_args
        assert len(call_args[1]["ids"]) == 2
        assert len(call_args[1]["embeddings"]) == 2

    @patch("agentnexus.codegraph.vector_store._get_codegraph_collection")
    def test_upsert_with_none_embeddings(self, mock_get_col, sample_nodes):
        mock_collection = MagicMock()
        mock_get_col.return_value = mock_collection

        embeddings = [None, None]
        count = upsert_nodes(sample_nodes, embeddings, "hash123")

        assert count == 0
        mock_collection.upsert.assert_not_called()

    @patch("agentnexus.codegraph.vector_store._get_codegraph_collection")
    def test_upsert_empty_nodes(self, mock_get_col):
        mock_collection = MagicMock()
        mock_get_col.return_value = mock_collection

        count = upsert_nodes([], [], "hash123")

        assert count == 0
        mock_collection.upsert.assert_not_called()

    @patch("agentnexus.codegraph.vector_store._get_codegraph_collection")
    def test_upsert_mixed_embeddings(self, mock_get_col, sample_nodes):
        mock_collection = MagicMock()
        mock_get_col.return_value = mock_collection

        embeddings = [[0.1, 0.2], None]
        count = upsert_nodes(sample_nodes, embeddings, "hash123")

        assert count == 1
        mock_collection.upsert.assert_called_once()

    @patch("agentnexus.codegraph.vector_store._get_codegraph_collection")
    def test_upsert_metadata(self, mock_get_col, sample_nodes, sample_embeddings):
        mock_collection = MagicMock()
        mock_get_col.return_value = mock_collection

        upsert_nodes(sample_nodes, sample_embeddings, "hash123")

        call_args = mock_collection.upsert.call_args
        metas = call_args[1]["metadatas"]
        assert metas[0]["file_path"] == "pkg/test.py"
        assert metas[0]["content_hash"] == "hash123"
        assert metas[0]["node_kind"] == "function"


class TestDeleteByFile:
    @patch("agentnexus.codegraph.vector_store._get_codegraph_collection")
    def test_delete_all_for_file(self, mock_get_col):
        mock_collection = MagicMock()
        mock_get_col.return_value = mock_collection
        mock_collection.get.return_value = {"ids": ["id1", "id2"]}

        count = delete_by_file("test.py")

        assert count == 2
        mock_collection.delete.assert_called_once_with(ids=["id1", "id2"])

    @patch("agentnexus.codegraph.vector_store._get_codegraph_collection")
    def test_delete_with_hash_exclusion(self, mock_get_col):
        mock_collection = MagicMock()
        mock_get_col.return_value = mock_collection
        mock_collection.get.return_value = {"ids": ["id1"]}

        count = delete_by_file("test.py", exclude_content_hash="new_hash")

        assert count == 1
        # Verify the where clause includes the hash filter
        call_args = mock_collection.get.call_args
        where = call_args[1]["where"]
        assert "$and" in where

    @patch("agentnexus.codegraph.vector_store._get_codegraph_collection")
    def test_delete_no_matches(self, mock_get_col):
        mock_collection = MagicMock()
        mock_get_col.return_value = mock_collection
        mock_collection.get.return_value = {"ids": []}

        count = delete_by_file("test.py")

        assert count == 0
        mock_collection.delete.assert_not_called()

    @patch("agentnexus.codegraph.vector_store._get_codegraph_collection")
    def test_delete_handles_exception(self, mock_get_col):
        mock_collection = MagicMock()
        mock_get_col.return_value = mock_collection
        mock_collection.get.side_effect = Exception("ChromaDB error")

        count = delete_by_file("test.py")

        assert count == 0


class TestDeleteByIds:
    @patch("agentnexus.codegraph.vector_store._get_codegraph_collection")
    def test_delete_by_ids(self, mock_get_col):
        mock_collection = MagicMock()
        mock_get_col.return_value = mock_collection

        count = delete_by_ids(["id1", "id2", "id3"])

        assert count == 3
        mock_collection.delete.assert_called_once_with(ids=["id1", "id2", "id3"])

    @patch("agentnexus.codegraph.vector_store._get_codegraph_collection")
    def test_delete_empty_ids(self, mock_get_col):
        mock_collection = MagicMock()
        mock_get_col.return_value = mock_collection

        count = delete_by_ids([])

        assert count == 0
        mock_collection.delete.assert_not_called()


class TestSearchSemantic:
    @patch("agentnexus.codegraph.vector_store._get_codegraph_collection")
    def test_basic_search(self, mock_get_col):
        mock_collection = MagicMock()
        mock_get_col.return_value = mock_collection
        mock_collection.query.return_value = {
            "ids": [["id1", "id2"]],
            "documents": [["doc1", "doc2"]],
            "distances": [[0.1, 0.3]],
            "metadatas": [[{"name": "func1"}, {"name": "func2"}]],
        }

        results = search_semantic([0.1, 0.2, 0.3], limit=10)

        assert len(results) == 2
        assert results[0]["id"] == "id1"
        assert results[0]["score"] == pytest.approx(0.9)
        assert results[1]["score"] == pytest.approx(0.7)

    @patch("agentnexus.codegraph.vector_store._get_codegraph_collection")
    def test_search_with_kind_filter(self, mock_get_col):
        mock_collection = MagicMock()
        mock_get_col.return_value = mock_collection
        mock_collection.query.return_value = {
            "ids": [["id1"]],
            "documents": [["doc1"]],
            "distances": [[0.2]],
            "metadatas": [[{"name": "func1"}]],
        }

        search_semantic([0.1, 0.2], limit=5, kind="function")

        call_args = mock_collection.query.call_args
        where = call_args[1]["where"]
        assert where == {"node_kind": {"$eq": "function"}}

    @patch("agentnexus.codegraph.vector_store._get_codegraph_collection")
    def test_search_with_language_filter(self, mock_get_col):
        mock_collection = MagicMock()
        mock_get_col.return_value = mock_collection
        mock_collection.query.return_value = {
            "ids": [["id1"]],
            "documents": [["doc1"]],
            "distances": [[0.2]],
            "metadatas": [[{}]],
        }

        search_semantic([0.1, 0.2], limit=5, language="python")

        call_args = mock_collection.query.call_args
        where = call_args[1]["where"]
        assert where == {"language": {"$eq": "python"}}

    @patch("agentnexus.codegraph.vector_store._get_codegraph_collection")
    def test_search_with_multiple_filters(self, mock_get_col):
        mock_collection = MagicMock()
        mock_get_col.return_value = mock_collection
        mock_collection.query.return_value = {
            "ids": [[]],
            "documents": [[]],
            "distances": [[]],
            "metadatas": [[]],
        }

        search_semantic([0.1], limit=5, kind="function", language="python")

        call_args = mock_collection.query.call_args
        where = call_args[1]["where"]
        assert "$and" in where

    @patch("agentnexus.codegraph.vector_store._get_codegraph_collection")
    def test_search_empty_results(self, mock_get_col):
        mock_collection = MagicMock()
        mock_get_col.return_value = mock_collection
        mock_collection.query.return_value = {
            "ids": [[]],
            "documents": [[]],
            "distances": [[]],
            "metadatas": [[]],
        }

        results = search_semantic([0.1, 0.2])

        assert results == []


class TestGetAllIds:
    @patch("agentnexus.codegraph.vector_store._get_codegraph_collection")
    def test_returns_ids(self, mock_get_col):
        mock_collection = MagicMock()
        mock_get_col.return_value = mock_collection
        mock_collection.get.return_value = {"ids": ["id1", "id2", "id3"]}

        ids = get_all_ids()

        assert ids == {"id1", "id2", "id3"}

    @patch("agentnexus.codegraph.vector_store._get_codegraph_collection")
    def test_handles_exception(self, mock_get_col):
        mock_collection = MagicMock()
        mock_get_col.return_value = mock_collection
        mock_collection.get.side_effect = Exception("error")

        ids = get_all_ids()

        assert ids == set()


class TestClearCollection:
    @patch("agentnexus.storage.chroma.delete_collection")
    def test_calls_delete_collection(self, mock_delete):
        clear_collection()
        mock_delete.assert_called_once_with(name=COLLECTION_NAME)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
