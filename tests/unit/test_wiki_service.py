"""Tests for agentnexus.wiki.wiki_service — main wiki orchestration service."""

from unittest.mock import MagicMock, patch

from agentnexus.wiki.models import (
    ConfidenceLevel,
    QueryDecision,
    SynthesisLevel,
    WikiPage,
    WikiStatement,
)
from agentnexus.wiki.wiki_service import WikiQueryResult, WikiService


def _make_page(
    page_id: str = "p1",
    title: str = "Test Page",
    confidence: str = ConfidenceLevel.HIGH.value,
    ns: str = "default",
    content: str = "Some content",
) -> WikiPage:
    return WikiPage(
        page_id=page_id,
        title=title,
        content=content,
        confidence=confidence,
        source_namespace=ns,
        statements=[
            WikiStatement(
                statement_id="s1",
                page_id=page_id,
                text="A claim.",
                synthesis_level=SynthesisLevel.DIRECT_QUOTE.value,
                source_chunk_ids=["c1"],
            )
        ],
    )


class TestWikiQueryResult:
    def test_default_values(self):
        result = WikiQueryResult()
        assert result.used_wiki is False
        assert result.decision == ""
        assert result.answer == ""
        assert result.source_chunks == []
        assert result.disclaimer == ""
        assert result.rag_results == []


class TestWikiServiceQuery:
    def setup_method(self):
        self.mock_store = MagicMock()
        self.mock_verifier = MagicMock()
        self.mock_router = MagicMock()
        self.service = WikiService(
            store=self.mock_store,
            verifier=self.mock_verifier,
            router=self.mock_router,
        )

    @patch.object(WikiService, "search_wiki_pages")
    def test_force_rag_skips_wiki_search(self, mock_search):
        with patch.object(self.service, "_rag_fallback") as mock_rag:
            mock_rag.return_value = WikiQueryResult(used_wiki=False, decision="fallback_to_rag")
            self.service.query("test question", "ns1", force_rag=True)
        mock_search.assert_not_called()

    @patch.object(WikiService, "search_wiki_pages")
    def test_no_wiki_results_falls_back_to_rag(self, mock_search):
        mock_search.return_value = []
        with patch.object(self.service, "_rag_fallback") as mock_rag:
            mock_rag.return_value = WikiQueryResult(used_wiki=False)
            result = self.service.query("question", "ns1")
        assert result.used_wiki is False

    @patch.object(WikiService, "search_wiki_pages")
    def test_untrusted_page_falls_back_to_rag(self, mock_search):
        page = _make_page(confidence=ConfidenceLevel.UNTRUSTED.value)
        mock_search.return_value = [page]
        self.mock_router.route.return_value = QueryDecision.FALLBACK_TO_RAG
        with patch.object(self.service, "_rag_fallback") as mock_rag:
            mock_rag.return_value = WikiQueryResult(used_wiki=False)
            result = self.service.query("question", "ns1")
        assert result.used_wiki is False

    @patch.object(WikiService, "search_wiki_pages")
    def test_high_confidence_returns_wiki_answer(self, mock_search):
        page = _make_page(confidence=ConfidenceLevel.HIGH.value)
        mock_search.return_value = [page]
        self.mock_router.route.return_value = QueryDecision.USE_WIKI
        result = self.service.query("question", "ns1")
        assert result.used_wiki is True
        assert result.decision == QueryDecision.USE_WIKI.value

    @patch.object(WikiService, "search_wiki_pages")
    def test_medium_confidence_includes_source_chunks(self, mock_search):
        page = _make_page(confidence=ConfidenceLevel.MEDIUM.value)
        mock_search.return_value = [page]
        self.mock_router.route.return_value = QueryDecision.USE_WIKI_WITH_SOURCES
        self.mock_router.get_source_chunks.return_value = ["c1", "c2"]
        result = self.service.query("question", "ns1")
        assert result.used_wiki is True
        assert result.source_chunks == ["c1", "c2"]

    @patch.object(WikiService, "search_wiki_pages")
    def test_low_confidence_includes_disclaimer(self, mock_search):
        page = _make_page(confidence=ConfidenceLevel.LOW.value)
        mock_search.return_value = [page]
        self.mock_router.route.return_value = QueryDecision.USE_WIKI_WITH_DISCLAIMER
        self.mock_router.get_source_chunks.return_value = ["c1"]
        self.mock_router.build_disclaimer.return_value = "Warning: synthesized content"
        result = self.service.query("question", "ns1")
        assert result.disclaimer == "Warning: synthesized content"


class TestWikiServiceLint:
    def setup_method(self):
        self.mock_store = MagicMock()
        self.service = WikiService(store=self.mock_store)

    def test_run_lint_returns_item_dicts(self):
        mock_item = MagicMock()
        mock_item.item_id = "i1"
        mock_item.priority = 1
        mock_item.page_id = "p1"
        mock_item.description = "Test"
        with patch.object(self.service.linter, "run_full_lint", return_value=[mock_item]):
            result = self.service.run_lint("ns1")
        assert len(result) == 1
        assert result[0]["item_id"] == "i1"

    def test_process_overdue_reviews_delegates_to_linter(self):
        with patch.object(self.service.linter, "process_overdue_items", return_value=[{"action": "test"}]):
            result = self.service.process_overdue_reviews()
        assert result == [{"action": "test"}]


class TestWikiServiceCalibration:
    def setup_method(self):
        self.mock_store = MagicMock()
        self.mock_verifier = MagicMock()
        self.mock_router = MagicMock()
        self.service = WikiService(
            store=self.mock_store,
            verifier=self.mock_verifier,
            router=self.mock_router,
        )

    @patch("agentnexus.wiki.wiki_service.run_calibration")
    def test_calibrate_updates_verifier(self, mock_run_cal):
        mock_run_cal.return_value = {
            "thresholds": {"jaccard_direct_quote": 0.7},
            "confusion_matrix": {},
            "sample_size": 10,
            "rounds": 2,
        }
        result = self.service.calibrate([])
        assert result["sample_size"] == 10
        # Verifier should be replaced
        assert self.service.verifier is not self.mock_verifier

    def test_check_calibration_needed_true_when_no_calibration(self):
        self.mock_store.get_latest_calibration.return_value = None
        self.mock_store.get_stats.return_value = {"page_count": 5}
        assert self.service.check_calibration_needed() is True

    def test_check_calibration_needed_false_when_small_growth(self):
        self.mock_store.get_latest_calibration.return_value = {"sample_size": 10}
        self.mock_store.get_stats.return_value = {"page_count": 11}
        with patch("agentnexus.wiki.wiki_service.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(wiki_calibration_retrigger_pct=0.5)
            assert self.service.check_calibration_needed() is False

    def test_check_calibration_needed_true_when_large_growth(self):
        self.mock_store.get_latest_calibration.return_value = {"sample_size": 10}
        self.mock_store.get_stats.return_value = {"page_count": 20}
        with patch("agentnexus.wiki.wiki_service.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(wiki_calibration_retrigger_pct=0.5)
            assert self.service.check_calibration_needed() is True


class TestWikiServiceStats:
    def setup_method(self):
        self.mock_store = MagicMock()
        self.service = WikiService(store=self.mock_store)

    def test_get_stats_includes_calibration_flag(self):
        self.mock_store.get_stats.return_value = {"page_count": 5, "statement_count": 10}
        self.mock_store.get_latest_calibration.return_value = None
        with patch.object(self.service, "check_calibration_needed", return_value=True):
            stats = self.service.get_stats()
        assert stats["calibration_needed"] is True
        assert stats["page_count"] == 5


class TestWikiServiceRagIntegration:
    def setup_method(self):
        self.mock_store = MagicMock()
        self.mock_verifier = MagicMock()
        self.mock_router = MagicMock()
        self.service = WikiService(
            store=self.mock_store,
            verifier=self.mock_verifier,
            router=self.mock_router,
        )

    @patch("agentnexus.wiki.wiki_service.get_settings")
    def test_on_rag_ingest_skips_when_wiki_disabled(self, mock_settings):
        mock_settings.return_value = MagicMock(wiki_enabled=False)
        with patch.object(self.service.propagation, "on_chunk_update") as mock_update:
            self.service.on_rag_ingest(["c1"])
        mock_update.assert_not_called()

    @patch("agentnexus.wiki.wiki_service.get_settings")
    def test_on_rag_ingest_triggers_propagation(self, mock_settings):
        mock_settings.return_value = MagicMock(wiki_enabled=True)
        with patch.object(self.service.propagation, "on_chunk_update") as mock_update:
            self.service.on_rag_ingest(["c1", "c2"])
        mock_update.assert_called_once_with(["c1", "c2"])
