"""Tests for agentnexus.wiki.propagation — trust propagation engine."""

from unittest.mock import MagicMock, patch

from agentnexus.wiki.models import (
    ConfidenceLevel,
    SynthesisLevel,
    WikiPage,
    WikiStatement,
)
from agentnexus.wiki.propagation import PropagationEngine


def _make_page(page_id: str, confidence: str = ConfidenceLevel.HIGH.value) -> WikiPage:
    return WikiPage(page_id=page_id, title=f"Page {page_id}", confidence=confidence)


class TestPropagateDegradation:
    def setup_method(self):
        self.mock_store = MagicMock()
        self.mock_verifier = MagicMock()
        self.mock_router = MagicMock()
        self.engine = PropagationEngine(
            store=self.mock_store,
            verifier=self.mock_verifier,
            router=self.mock_router,
            max_depth=3,
        )

    def test_no_propagation_when_no_dependents(self):
        self.mock_store.get_page.return_value = _make_page("p1", ConfidenceLevel.HIGH.value)
        self.mock_store.list_dependents.return_value = []
        self.engine.propagate_degradation("p1")
        self.mock_store.update_page_confidence.assert_not_called()

    def test_propagates_min_confidence_to_dependent(self):
        self.mock_store.get_page.side_effect = lambda pid, include_statements=True: {
            "p1": _make_page("p1", ConfidenceLevel.LOW.value),
            "p2": _make_page("p2", ConfidenceLevel.HIGH.value),
        }.get(pid)
        self.mock_store.list_dependents.side_effect = lambda pid: {
            "p1": ["p2"],
            "p2": [],
        }.get(pid, [])
        self.mock_router.min_confidence.return_value = ConfidenceLevel.LOW.value

        self.engine.propagate_degradation("p1")
        self.mock_store.update_page_confidence.assert_called_once_with(
            "p2", ConfidenceLevel.LOW.value, flag="depends_on_degraded_page:p1"
        )

    def test_skips_update_when_confidence_unchanged(self):
        self.mock_store.get_page.side_effect = lambda pid, include_statements=True: {
            "p1": _make_page("p1", ConfidenceLevel.LOW.value),
            "p2": _make_page("p2", ConfidenceLevel.LOW.value),
        }.get(pid)
        self.mock_store.list_dependents.side_effect = lambda pid: {
            "p1": ["p2"],
            "p2": [],
        }.get(pid, [])
        self.mock_router.min_confidence.return_value = ConfidenceLevel.LOW.value

        self.engine.propagate_degradation("p1")
        self.mock_store.update_page_confidence.assert_not_called()

    def test_respects_max_depth(self):
        self.mock_store.get_page.return_value = _make_page("p1", ConfidenceLevel.HIGH.value)
        self.mock_store.list_dependents.return_value = ["p2"]
        self.engine.propagate_degradation("p1", depth=3)
        # At max depth, should not propagate
        self.mock_store.list_dependents.assert_not_called()

    def test_skips_missing_dependent_page(self):
        self.mock_store.get_page.side_effect = lambda pid, include_statements=True: {
            "p1": _make_page("p1", ConfidenceLevel.LOW.value),
        }.get(pid)
        self.mock_store.list_dependents.return_value = ["p2"]
        self.mock_router.min_confidence.return_value = ConfidenceLevel.LOW.value
        self.engine.propagate_degradation("p1")
        self.mock_store.update_page_confidence.assert_not_called()


class TestPropagateRecovery:
    def setup_method(self):
        self.mock_store = MagicMock()
        self.mock_verifier = MagicMock()
        self.mock_router = MagicMock()
        self.engine = PropagationEngine(
            store=self.mock_store,
            verifier=self.mock_verifier,
            router=self.mock_router,
            max_depth=3,
        )

    def test_no_action_when_no_dependents(self):
        self.mock_store.list_dependents.return_value = []
        self.engine.propagate_recovery("p1")
        self.mock_store.get_page.assert_not_called()

    def test_reverifies_dependent_pages(self):
        self.mock_store.list_dependents.side_effect = lambda pid: {
            "p1": ["p2"],
            "p2": [],
        }.get(pid, [])

        with patch.object(self.engine, "_reverify_page") as mock_reverify:
            self.engine.propagate_recovery("p1")
            mock_reverify.assert_called_once_with("p2")

    def test_respects_max_depth(self):
        self.mock_store.list_dependents.return_value = ["p2"]
        self.engine.propagate_recovery("p1", depth=3)
        self.mock_store.list_dependents.assert_not_called()


class TestOnChunkUpdate:
    def setup_method(self):
        self.mock_store = MagicMock()
        self.mock_verifier = MagicMock()
        self.mock_router = MagicMock()
        self.engine = PropagationEngine(
            store=self.mock_store,
            verifier=self.mock_verifier,
            router=self.mock_router,
            max_depth=3,
        )

    def test_no_action_when_empty_chunk_ids(self):
        self.engine.on_chunk_update([])
        self.mock_store.find_statements_by_chunks.assert_not_called()

    def test_reverifies_affected_statements(self):
        stmt = WikiStatement(
            statement_id="s1",
            page_id="p1",
            text="test",
            synthesis_level=SynthesisLevel.DIRECT_QUOTE.value,
            source_chunk_ids=["c1"],
        )
        self.mock_store.find_statements_by_chunks.return_value = [stmt]
        self.mock_verifier.verify_statement.return_value = SynthesisLevel.DIRECT_QUOTE.value
        self.mock_router.is_degradation.return_value = False

        with patch.object(self.engine, "_get_chunk_texts", return_value={"c1": "text"}):
            self.engine.on_chunk_update(["c1"])

        self.mock_store.find_statements_by_chunks.assert_called_once_with(["c1"])

    def test_updates_synthesis_level_on_change(self):
        stmt = WikiStatement(
            statement_id="s1",
            page_id="p1",
            text="test",
            synthesis_level=SynthesisLevel.DIRECT_QUOTE.value,
            source_chunk_ids=["c1"],
        )
        self.mock_store.find_statements_by_chunks.return_value = [stmt]
        self.mock_verifier.verify_statement.return_value = SynthesisLevel.SYNTHESIS.value
        self.mock_router.is_degradation.return_value = True
        self.mock_store.get_page.return_value = _make_page("p1", ConfidenceLevel.HIGH.value)
        self.mock_router.compute_page_confidence.return_value = ConfidenceLevel.LOW.value

        with (
            patch.object(self.engine, "_get_chunk_texts", return_value={"c1": "text"}),
            patch.object(self.engine, "propagate_degradation"),
        ):
            self.engine.on_chunk_update(["c1"])

        self.mock_store.update_statement_synthesis_level.assert_called_once_with(
            "s1", SynthesisLevel.SYNTHESIS.value
        )
