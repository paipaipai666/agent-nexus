"""Tests for agentnexus.wiki.store — SQLite-backed wiki storage."""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agentnexus.wiki.models import (
    CanonicalDefinition,
    DefinitionEntry,
    ReviewItem,
    ReviewPriority,
    ReviewStatus,
    WikiPage,
    WikiStatement,
)
from agentnexus.wiki.store import WikiStore, _encode_json, _decode_json, _encode_definitions, _decode_definitions


@pytest.fixture
def wiki_store(tmp_path):
    """Create a WikiStore backed by a temporary SQLite database."""
    db_path = str(tmp_path / "test_wiki.db")
    with patch("agentnexus.wiki.store.get_settings") as mock_settings:
        mock_settings.return_value = MagicMock(rag_catalog_db_path=db_path)
        store = WikiStore(db_path=db_path)
    yield store
    store.close()


def _make_page(page_id: str = "p1", title: str = "Test Page", ns: str = "default") -> WikiPage:
    return WikiPage(
        page_id=page_id,
        title=title,
        page_type="concept",
        content="Some content",
        confidence="high",
        source_namespace=ns,
    )


def _make_stmt(
    stmt_id: str = "s1",
    page_id: str = "p1",
    text: str = "A claim.",
    source_chunk_ids: list[str] | None = None,
) -> WikiStatement:
    return WikiStatement(
        statement_id=stmt_id,
        page_id=page_id,
        text=text,
        synthesis_level="direct_quote",
        source_chunk_ids=source_chunk_ids if source_chunk_ids is not None else ["c1"],
    )


class TestJsonHelpers:
    def test_encode_decode_roundtrip(self):
        data = {"key": "value", "number": 42}
        encoded = _encode_json(data)
        assert _decode_json(encoded) == data

    def test_decode_json_empty_returns_empty_dict(self):
        assert _decode_json("") == {}
        assert _decode_json(None) == {}

    def test_encode_decode_definitions(self):
        defs = [DefinitionEntry(text="def1", source_chunk_id="c1", confidence=0.9)]
        encoded = _encode_definitions(defs)
        decoded = _decode_definitions(encoded)
        assert len(decoded) == 1
        assert decoded[0].text == "def1"


class TestWikiStorePages:
    def test_upsert_and_get_page(self, wiki_store):
        page = _make_page()
        wiki_store.upsert_page(page)
        loaded = wiki_store.get_page("p1")
        assert loaded is not None
        assert loaded.page_id == "p1"
        assert loaded.title == "Test Page"
        assert loaded.confidence == "high"

    def test_get_nonexistent_page_returns_none(self, wiki_store):
        assert wiki_store.get_page("nonexistent") is None

    def test_upsert_updates_existing_page(self, wiki_store):
        wiki_store.upsert_page(_make_page(title="Original"))
        wiki_store.upsert_page(_make_page(title="Updated"))
        loaded = wiki_store.get_page("p1")
        assert loaded.title == "Updated"

    def test_list_pages_returns_all(self, wiki_store):
        wiki_store.upsert_page(_make_page("p1", "Page A"))
        wiki_store.upsert_page(_make_page("p2", "Page B"))
        pages = wiki_store.list_pages()
        assert len(pages) == 2

    def test_list_pages_filters_by_namespace(self, wiki_store):
        wiki_store.upsert_page(_make_page("p1", ns="ns1"))
        wiki_store.upsert_page(_make_page("p2", ns="ns2"))
        pages = wiki_store.list_pages(source_namespace="ns1")
        assert len(pages) == 1
        assert pages[0].source_namespace == "ns1"

    def test_delete_page(self, wiki_store):
        wiki_store.upsert_page(_make_page())
        wiki_store.delete_page("p1")
        assert wiki_store.get_page("p1") is None

    def test_update_page_confidence(self, wiki_store):
        wiki_store.upsert_page(_make_page())
        wiki_store.update_page_confidence("p1", "low", flag="test_flag")
        loaded = wiki_store.get_page("p1")
        assert loaded.confidence == "low"
        assert "test_flag" in loaded.flags

    def test_update_page_confidence_without_flag(self, wiki_store):
        wiki_store.upsert_page(_make_page())
        wiki_store.update_page_confidence("p1", "untrusted")
        loaded = wiki_store.get_page("p1")
        assert loaded.confidence == "untrusted"


class TestWikiStoreStatements:
    def test_upsert_and_get_statement(self, wiki_store):
        wiki_store.upsert_page(_make_page())
        stmt = _make_stmt()
        wiki_store.upsert_statement(stmt)
        loaded = wiki_store.get_statement("s1")
        assert loaded is not None
        assert loaded.text == "A claim."

    def test_list_statements_for_page(self, wiki_store):
        wiki_store.upsert_page(_make_page())
        wiki_store.upsert_statement(_make_stmt("s1", "p1", "Claim 1"))
        wiki_store.upsert_statement(_make_stmt("s2", "p1", "Claim 2"))
        stmts = wiki_store.list_statements("p1")
        assert len(stmts) == 2

    def test_find_statements_by_chunks(self, wiki_store):
        wiki_store.upsert_page(_make_page())
        stmt = _make_stmt(source_chunk_ids=["c1", "c2"])
        wiki_store.upsert_statement(stmt)
        found = wiki_store.find_statements_by_chunks(["c2"])
        assert len(found) == 1
        assert found[0].statement_id == "s1"

    def test_find_statements_by_chunks_empty_input(self, wiki_store):
        assert wiki_store.find_statements_by_chunks([]) == []

    def test_update_statement_synthesis_level(self, wiki_store):
        wiki_store.upsert_page(_make_page())
        wiki_store.upsert_statement(_make_stmt())
        wiki_store.update_statement_synthesis_level("s1", "synthesis")
        loaded = wiki_store.get_statement("s1")
        assert loaded.verified_synthesis_level == "synthesis"


class TestWikiStoreCanonicalDefinitions:
    def test_upsert_and_load_definitions(self, wiki_store):
        wiki_store.upsert_page(_make_page())
        canon = CanonicalDefinition(
            definitions=[DefinitionEntry(text="def1", source_chunk_id="c1", confidence=0.9)],
            consensus="canonical text",
            divergence=0.1,
            last_recalculated="2024-01-01T00:00:00",
        )
        wiki_store.upsert_canonical_definition("p1", "api", canon)
        loaded = wiki_store.get_page("p1")
        assert "api" in loaded.canonical_definitions
        assert loaded.canonical_definitions["api"].consensus == "canonical text"


class TestWikiStoreDependencyGraph:
    def test_add_and_list_dependents(self, wiki_store):
        wiki_store.upsert_page(_make_page("p1"))
        wiki_store.upsert_page(_make_page("p2"))
        wiki_store.add_dependency("p1", "p2")
        deps = wiki_store.list_dependents("p1")
        assert "p2" in deps

    def test_list_dependencies(self, wiki_store):
        wiki_store.upsert_page(_make_page("p1"))
        wiki_store.upsert_page(_make_page("p2"))
        wiki_store.add_dependency("p1", "p2")
        deps = wiki_store.list_dependencies("p2")
        assert "p1" in deps

    def test_remove_dependencies(self, wiki_store):
        wiki_store.upsert_page(_make_page("p1"))
        wiki_store.upsert_page(_make_page("p2"))
        wiki_store.add_dependency("p1", "p2")
        wiki_store.remove_dependencies("p1")
        assert wiki_store.list_dependents("p1") == []


class TestWikiStoreReviewQueue:
    def test_add_and_list_review_items(self, wiki_store):
        item = ReviewItem(
            item_id="r1",
            priority=ReviewPriority.DEFINITION_CONFLICT.value,
            page_id="p1",
            description="Test item",
            status=ReviewStatus.PENDING.value,
            deadline="2099-01-01T00:00:00",
            created_at="2024-01-01T00:00:00",
        )
        wiki_store.add_review_item(item)
        items = wiki_store.list_review_items()
        assert len(items) == 1
        assert items[0].item_id == "r1"

    def test_resolve_review_item(self, wiki_store):
        item = ReviewItem(
            item_id="r1",
            priority=1,
            page_id="p1",
            description="Test",
            status=ReviewStatus.PENDING.value,
            deadline="2099-01-01T00:00:00",
            created_at="2024-01-01T00:00:00",
        )
        wiki_store.add_review_item(item)
        wiki_store.resolve_review_item("r1", ReviewStatus.RESOLVED.value)
        items = wiki_store.list_review_items(status=ReviewStatus.RESOLVED.value)
        assert len(items) == 1

    def test_get_overdue_review_items(self, wiki_store):
        item = ReviewItem(
            item_id="r1",
            priority=1,
            page_id="p1",
            description="Overdue",
            status=ReviewStatus.PENDING.value,
            deadline="2020-01-01T00:00:00",  # Past deadline
            created_at="2024-01-01T00:00:00",
        )
        wiki_store.add_review_item(item)
        overdue = wiki_store.get_overdue_review_items()
        assert len(overdue) == 1


class TestWikiStoreCalibration:
    def test_save_and_get_calibration(self, wiki_store):
        thresholds = {"jaccard_direct_quote": 0.6}
        cm = {"matrix": {}, "false_degradation_rate": 0.0}
        wiki_store.save_calibration(thresholds, cm, sample_size=100)
        result = wiki_store.get_latest_calibration()
        assert result is not None
        assert result["sample_size"] == 100
        assert result["thresholds"]["jaccard_direct_quote"] == 0.6

    def test_get_calibration_returns_none_when_empty(self, wiki_store):
        assert wiki_store.get_latest_calibration() is None


class TestWikiStoreStats:
    def test_get_stats_empty_db(self, wiki_store):
        stats = wiki_store.get_stats()
        assert stats["page_count"] == 0
        assert stats["statement_count"] == 0
        assert stats["pending_reviews"] == 0

    def test_get_stats_with_data(self, wiki_store):
        wiki_store.upsert_page(_make_page())
        wiki_store.upsert_statement(_make_stmt())
        stats = wiki_store.get_stats()
        assert stats["page_count"] == 1
        assert stats["statement_count"] == 1
        assert "high" in stats["confidence_distribution"]
