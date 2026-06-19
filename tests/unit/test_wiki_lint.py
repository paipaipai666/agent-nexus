"""Tests for agentnexus.wiki.lint — consistency, drift detection, coverage, and linter orchestration."""

from unittest.mock import MagicMock, patch
from datetime import datetime, timezone

from agentnexus.wiki.lint import (
    ConsistencyChecker,
    CoverageChecker,
    DriftDetector,
    WikiLinter,
    _deadline,
    _utc_now,
)
from agentnexus.wiki.models import (
    CanonicalDefinition,
    DefinitionEntry,
    ReviewItem,
    ReviewPriority,
    ReviewStatus,
    WikiPage,
    WikiStatement,
)


def _make_page(
    page_id: str = "p1",
    canonical_definitions: dict[str, CanonicalDefinition] | None = None,
    statements: list[WikiStatement] | None = None,
) -> WikiPage:
    return WikiPage(
        page_id=page_id,
        title=f"Page {page_id}",
        statements=statements or [],
        canonical_definitions=canonical_definitions or {},
    )


def _make_canonical_def(consensus: str, divergence: float = 0.0) -> CanonicalDefinition:
    return CanonicalDefinition(
        definitions=[DefinitionEntry(text=consensus, source_chunk_id="c1", confidence=0.9)],
        consensus=consensus,
        divergence=divergence,
        last_recalculated="2024-01-01T00:00:00",
    )


class TestUtcNow:
    def test_returns_iso_format_string(self):
        result = _utc_now()
        # Should be parseable ISO format
        datetime.fromisoformat(result)

    def test_has_no_microseconds(self):
        result = _utc_now()
        assert "." not in result


@patch("agentnexus.wiki.lint.get_settings")
class TestDeadline:
    def test_returns_iso_future_date(self, mock_settings):
        mock_settings.return_value = MagicMock(
            wiki_review_sla_p1_days=7,
            wiki_review_sla_p2_days=14,
            wiki_review_sla_p3_days=30,
        )
        result = _deadline(ReviewPriority.DEFINITION_CONFLICT.value)
        deadline = datetime.fromisoformat(result)
        now = datetime.now(timezone.utc)
        assert deadline > now

    def test_p1_deadline_is_shorter_than_p3(self, mock_settings):
        mock_settings.return_value = MagicMock(
            wiki_review_sla_p1_days=7,
            wiki_review_sla_p2_days=14,
            wiki_review_sla_p3_days=30,
        )
        p1 = datetime.fromisoformat(_deadline(ReviewPriority.DEFINITION_CONFLICT.value))
        p3 = datetime.fromisoformat(_deadline(ReviewPriority.COVERAGE_GAP.value))
        assert p1 < p3


@patch("agentnexus.wiki.lint.cosine_similarity")
class TestConsistencyChecker:
    def test_no_items_when_single_definition_per_term(self, mock_cosine):
        mock_store = MagicMock()
        mock_store.list_pages.return_value = [
            _make_page(canonical_definitions={"api": _make_canonical_def("REST interface")}),
        ]
        checker = ConsistencyChecker()
        items = checker.check(mock_store)
        assert items == []

    def test_no_items_when_definitions_agree(self, mock_cosine):
        mock_cosine.return_value = 0.9  # High similarity
        mock_store = MagicMock()
        mock_store.list_pages.return_value = [
            _make_page("p1", {"api": _make_canonical_def("REST interface")}),
            _make_page("p2", {"api": _make_canonical_def("RESTful interface")}),
        ]
        checker = ConsistencyChecker()
        items = checker.check(mock_store)
        assert items == []

    def test_detects_contradiction_when_low_similarity(self, mock_cosine):
        mock_cosine.return_value = 0.1  # Low similarity
        mock_store = MagicMock()
        mock_store.list_pages.return_value = [
            _make_page("p1", {"api": _make_canonical_def("REST interface")}),
            _make_page("p2", {"api": _make_canonical_def("GraphQL schema")}),
        ]
        checker = ConsistencyChecker()
        items = checker.check(mock_store)
        assert len(items) == 1
        assert items[0].priority == ReviewPriority.DEFINITION_CONFLICT.value
        assert items[0].status == ReviewStatus.PENDING.value

    def test_skips_definitions_without_consensus(self, mock_cosine):
        mock_store = MagicMock()
        mock_store.list_pages.return_value = [
            _make_page("p1", {"api": _make_canonical_def(None, divergence=0.5)}),
            _make_page("p2", {"api": _make_canonical_def("REST interface")}),
        ]
        checker = ConsistencyChecker()
        items = checker.check(mock_store)
        assert items == []


@patch("agentnexus.wiki.lint.cosine_similarity")
class TestDriftDetector:
    def test_no_items_when_statement_matches_canonical(self, mock_cosine):
        mock_cosine.return_value = 0.9
        mock_store = MagicMock()
        canon = _make_canonical_def("REST API")
        stmt = WikiStatement(
            statement_id="s1",
            page_id="p1",
            text="REST API definition",
            canonical_term="api",
        )
        page = _make_page(canonical_definitions={"api": canon})
        mock_store.list_pages.return_value = [page]
        mock_store.get_page.return_value = WikiPage(
            page_id="p1",
            title="P1",
            statements=[stmt],
            canonical_definitions={"api": canon},
        )
        with patch("agentnexus.wiki.lint.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(wiki_drift_threshold=0.5)
            detector = DriftDetector()
            items = detector.check(mock_store)
        assert items == []

    def test_detects_drift_when_below_threshold(self, mock_cosine):
        mock_cosine.return_value = 0.1
        mock_store = MagicMock()
        canon = _make_canonical_def("REST API")
        stmt = WikiStatement(
            statement_id="s1",
            page_id="p1",
            text="Something completely different",
            canonical_term="api",
        )
        page = _make_page(canonical_definitions={"api": canon})
        mock_store.list_pages.return_value = [page]
        mock_store.get_page.return_value = WikiPage(
            page_id="p1",
            title="P1",
            statements=[stmt],
            canonical_definitions={"api": canon},
        )
        with patch("agentnexus.wiki.lint.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                wiki_drift_threshold=0.5,
                wiki_review_sla_p1_days=7,
                wiki_review_sla_p2_days=14,
                wiki_review_sla_p3_days=30,
            )
            detector = DriftDetector()
            items = detector.check(mock_store)
        assert len(items) == 1
        assert items[0].priority == ReviewPriority.SEMANTIC_DRIFT.value

    def test_skips_statements_without_canonical_term(self, mock_cosine):
        mock_store = MagicMock()
        stmt = WikiStatement(statement_id="s1", page_id="p1", text="text", canonical_term=None)
        page = _make_page()
        mock_store.list_pages.return_value = [page]
        mock_store.get_page.return_value = WikiPage(
            page_id="p1", title="P1", statements=[stmt]
        )
        with patch("agentnexus.wiki.lint.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(wiki_drift_threshold=0.5)
            detector = DriftDetector()
            items = detector.check(mock_store)
        assert items == []


class TestWikiLinter:
    def test_run_full_lint_aggregates_all_checks(self):
        mock_store = MagicMock()
        mock_store.list_pages.return_value = []
        linter = WikiLinter(mock_store)

        with (
            patch.object(linter.consistency, "check", return_value=[MagicMock()]),
            patch.object(linter.drift, "check", return_value=[MagicMock(), MagicMock()]),
            patch.object(linter.coverage, "check", return_value=[MagicMock()]),
        ):
            items = linter.run_full_lint("ns1", "rag_ns")
        assert len(items) == 4

    def test_enqueue_items_calls_store(self):
        mock_store = MagicMock()
        linter = WikiLinter(mock_store)
        items = [ReviewItem(item_id="i1"), ReviewItem(item_id="i2")]
        linter.enqueue_items(items)
        assert mock_store.add_review_item.call_count == 2

    def test_process_overdue_items_auto_degrades_p1(self):
        mock_store = MagicMock()
        overdue_item = ReviewItem(
            item_id="i1",
            priority=ReviewPriority.DEFINITION_CONFLICT.value,
            page_id="p1",
            status=ReviewStatus.PENDING.value,
        )
        mock_store.get_overdue_review_items.return_value = [overdue_item]
        linter = WikiLinter(mock_store)
        actions = linter.process_overdue_items()
        assert len(actions) == 1
        assert actions[0]["action"] == "auto_degrade"
        assert actions[0]["new_confidence"] == "untrusted"

    def test_process_overdue_items_archives_p3(self):
        mock_store = MagicMock()
        overdue_item = ReviewItem(
            item_id="i3",
            priority=ReviewPriority.COVERAGE_GAP.value,
            page_id="",
            status=ReviewStatus.PENDING.value,
        )
        mock_store.get_overdue_review_items.return_value = [overdue_item]
        linter = WikiLinter(mock_store)
        actions = linter.process_overdue_items()
        assert len(actions) == 1
        assert actions[0]["action"] == "archive"
