"""Tests for agentnexus.wiki.confidence — confidence routing and page trust computation."""

from agentnexus.wiki.confidence import ConfidenceRouter
from agentnexus.wiki.models import (
    ConfidenceLevel,
    QueryDecision,
    SynthesisLevel,
    WikiPage,
    WikiStatement,
)


def _make_statement(
    synthesis_level: str = SynthesisLevel.DIRECT_QUOTE.value,
    verified_synthesis_level: str | None = None,
    source_chunk_ids: list[str] | None = None,
) -> WikiStatement:
    return WikiStatement(
        statement_id="s1",
        page_id="p1",
        text="test statement",
        synthesis_level=synthesis_level,
        verified_synthesis_level=verified_synthesis_level,
        source_chunk_ids=source_chunk_ids or ["c1"],
    )


def _make_page(
    statements: list[WikiStatement] | None = None,
    confidence: str = ConfidenceLevel.HIGH.value,
) -> WikiPage:
    return WikiPage(
        page_id="p1",
        title="Test Page",
        statements=statements or [],
        confidence=confidence,
    )


class TestComputePageConfidence:
    def setup_method(self):
        self.router = ConfidenceRouter()

    def test_empty_statements_returns_high(self):
        page = _make_page(statements=[])
        assert self.router.compute_page_confidence(page) == ConfidenceLevel.HIGH.value

    def test_all_direct_quote_returns_high(self):
        stmts = [_make_statement(SynthesisLevel.DIRECT_QUOTE.value) for _ in range(5)]
        page = _make_page(statements=stmts)
        assert self.router.compute_page_confidence(page) == ConfidenceLevel.HIGH.value

    def test_majority_paraphrase_returns_high(self):
        stmts = [
            _make_statement(SynthesisLevel.PARAPHRASE.value),
            _make_statement(SynthesisLevel.PARAPHRASE.value),
            _make_statement(SynthesisLevel.PARAPHRASE.value),
            _make_statement(SynthesisLevel.CROSS_REFERENCE.value),
            _make_statement(SynthesisLevel.CROSS_REFERENCE.value),
        ]
        page = _make_page(statements=stmts)
        # 60% high-trust -> below 80% threshold, above 50% -> medium
        assert self.router.compute_page_confidence(page) == ConfidenceLevel.MEDIUM.value

    def test_synthesis_only_returns_low(self):
        stmts = [
            _make_statement(SynthesisLevel.SYNTHESIS.value),
            _make_statement(SynthesisLevel.SYNTHESIS.value),
        ]
        page = _make_page(statements=stmts)
        assert self.router.compute_page_confidence(page) == ConfidenceLevel.LOW.value

    def test_any_untrusted_makes_page_untrusted(self):
        stmts = [
            _make_statement(SynthesisLevel.DIRECT_QUOTE.value),
            _make_statement(SynthesisLevel.DIRECT_QUOTE.value),
            _make_statement(SynthesisLevel.DIRECT_QUOTE.value,
                            verified_synthesis_level=ConfidenceLevel.UNTRUSTED.value),
        ]
        page = _make_page(statements=stmts)
        assert self.router.compute_page_confidence(page) == ConfidenceLevel.UNTRUSTED.value

    def test_uses_verified_level_over_assigned(self):
        stmts = [
            _make_statement(
                SynthesisLevel.SYNTHESIS.value,
                verified_synthesis_level=SynthesisLevel.DIRECT_QUOTE.value,
            ),
            _make_statement(
                SynthesisLevel.SYNTHESIS.value,
                verified_synthesis_level=SynthesisLevel.PARAPHRASE.value,
            ),
        ]
        page = _make_page(statements=stmts)
        # Both verified as high-trust -> 100% -> high
        assert self.router.compute_page_confidence(page) == ConfidenceLevel.HIGH.value


class TestRoute:
    def setup_method(self):
        self.router = ConfidenceRouter()

    def test_untrusted_page_falls_back_to_rag(self):
        page = _make_page(confidence=ConfidenceLevel.UNTRUSTED.value)
        assert self.router.route(page) == QueryDecision.FALLBACK_TO_RAG

    def test_high_confidence_uses_wiki_directly(self):
        page = _make_page(confidence=ConfidenceLevel.HIGH.value)
        assert self.router.route(page) == QueryDecision.USE_WIKI

    def test_medium_confidence_uses_wiki_with_sources(self):
        page = _make_page(confidence=ConfidenceLevel.MEDIUM.value)
        assert self.router.route(page) == QueryDecision.USE_WIKI_WITH_SOURCES

    def test_low_confidence_uses_wiki_with_disclaimer(self):
        page = _make_page(confidence=ConfidenceLevel.LOW.value)
        assert self.router.route(page) == QueryDecision.USE_WIKI_WITH_DISCLAIMER


class TestGetSourceChunks:
    def setup_method(self):
        self.router = ConfidenceRouter()

    def test_collects_unique_chunk_ids_sorted(self):
        stmts = [
            _make_statement(source_chunk_ids=["c2", "c1"]),
            _make_statement(source_chunk_ids=["c3", "c1"]),
        ]
        page = _make_page(statements=stmts)
        chunks = self.router.get_source_chunks(page)
        assert chunks == ["c1", "c2", "c3"]

    def test_empty_statements_returns_empty(self):
        page = _make_page(statements=[])
        assert self.router.get_source_chunks(page) == []


class TestBuildDisclaimer:
    def setup_method(self):
        self.router = ConfidenceRouter()

    def test_disclaimer_includes_synthesis_count(self):
        stmts = [
            _make_statement(SynthesisLevel.SYNTHESIS.value),
            _make_statement(SynthesisLevel.SYNTHESIS.value),
            _make_statement(SynthesisLevel.DIRECT_QUOTE.value),
        ]
        page = _make_page(statements=stmts)
        disclaimer = self.router.build_disclaimer(page)
        assert "2/3" in disclaimer
        assert "synthesized" in disclaimer.lower() or "synth" in disclaimer.lower()


class TestIsDegradation:
    def setup_method(self):
        self.router = ConfidenceRouter()

    def test_direct_quote_to_synthesis_is_degradation(self):
        assert self.router.is_degradation(
            SynthesisLevel.DIRECT_QUOTE.value,
            SynthesisLevel.SYNTHESIS.value,
        ) is True

    def test_synthesis_to_direct_quote_is_not_degradation(self):
        assert self.router.is_degradation(
            SynthesisLevel.SYNTHESIS.value,
            SynthesisLevel.DIRECT_QUOTE.value,
        ) is False

    def test_same_level_is_not_degradation(self):
        assert self.router.is_degradation(
            SynthesisLevel.PARAPHRASE.value,
            SynthesisLevel.PARAPHRASE.value,
        ) is False


class TestMinConfidence:
    def setup_method(self):
        self.router = ConfidenceRouter()

    def test_returns_lower_of_two(self):
        result = self.router.min_confidence(
            ConfidenceLevel.HIGH.value,
            ConfidenceLevel.LOW.value,
        )
        assert result == ConfidenceLevel.LOW.value

    def test_returns_first_when_equal(self):
        result = self.router.min_confidence(
            ConfidenceLevel.MEDIUM.value,
            ConfidenceLevel.MEDIUM.value,
        )
        assert result == ConfidenceLevel.MEDIUM.value

    def test_untrusted_is_lowest(self):
        result = self.router.min_confidence(
            ConfidenceLevel.HIGH.value,
            ConfidenceLevel.UNTRUSTED.value,
        )
        assert result == ConfidenceLevel.UNTRUSTED.value
