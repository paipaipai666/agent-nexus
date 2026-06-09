"""Confidence routing — rule-tree based, not formula-based.

No weighted formula that needs标定数据. Each rule is individually auditable
and modifiable. When enough query-feedback data accumulates, rules can be
replaced with a learned model.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .models import (
    ConfidenceLevel,
    QueryDecision,
    SynthesisLevel,
    WikiPage,
)

logger = logging.getLogger(__name__)

# Trust ordering: higher number = more trusted
_TRUST_RANK = {
    SynthesisLevel.DIRECT_QUOTE.value: 3,
    SynthesisLevel.PARAPHRASE.value: 2,
    SynthesisLevel.CROSS_REFERENCE.value: 1,
    SynthesisLevel.SYNTHESIS.value: 0,
}


@dataclass
class QueryRouteResult:
    """Result of routing a query through the confidence system."""

    decision: QueryDecision
    confidence: str
    wiki_answer: str = ""
    source_chunks: list[str] | None = None
    disclaimer: str = ""
    penetration_link: str = ""


class ConfidenceRouter:
    """Rule-tree confidence computation and query routing.

    Rules (evaluated in order, first match wins):
    1. Any statement is untrusted → page is untrusted → fallback to RAG
    2. 80%+ statements are direct_quote or paraphrase → high → use wiki
    3. 50%+ are high-trust → medium → use wiki with source chunks
    4. Has synthesis statements → low → use wiki with disclaimer
    5. Otherwise → medium (default)
    """

    def compute_page_confidence(self, page: WikiPage) -> str:
        """Compute page-level confidence from its statements' synthesis levels."""
        levels = [
            s.verified_synthesis_level or s.synthesis_level
            for s in page.statements
        ]

        if not levels:
            return ConfidenceLevel.HIGH.value

        # Rule 1: Any untrusted → page is untrusted
        if any(lv == ConfidenceLevel.UNTRUSTED.value for lv in levels):
            return ConfidenceLevel.UNTRUSTED.value

        # Count high-trust statements
        high_count = sum(
            1 for lv in levels
            if lv in (SynthesisLevel.DIRECT_QUOTE.value, SynthesisLevel.PARAPHRASE.value)
        )
        total = len(levels)
        ratio = high_count / total

        # Rule 2: 80%+ high-trust → high confidence
        if ratio >= 0.8:
            return ConfidenceLevel.HIGH.value

        # Rule 3: 50%+ high-trust → medium
        if ratio >= 0.5:
            return ConfidenceLevel.MEDIUM.value

        # Rule 4: Has synthesis statements → low
        if any(lv == SynthesisLevel.SYNTHESIS.value for lv in levels):
            return ConfidenceLevel.LOW.value

        # Rule 5: Default
        return ConfidenceLevel.MEDIUM.value

    def route(self, page: WikiPage) -> QueryDecision:
        """Decide how to handle a query based on page confidence."""
        confidence = page.confidence

        # Rule 1: untrusted → force RAG
        if confidence == ConfidenceLevel.UNTRUSTED.value:
            return QueryDecision.FALLBACK_TO_RAG

        # Rule 2: high → use wiki directly
        if confidence == ConfidenceLevel.HIGH.value:
            return QueryDecision.USE_WIKI

        # Rule 3: medium → wiki with source chunks
        if confidence == ConfidenceLevel.MEDIUM.value:
            return QueryDecision.USE_WIKI_WITH_SOURCES

        # Rule 4: low → wiki with disclaimer
        return QueryDecision.USE_WIKI_WITH_DISCLAIMER

    def get_source_chunks(self, page: WikiPage) -> list[str]:
        """Collect all unique source chunk IDs from page statements."""
        chunks: set[str] = set()
        for stmt in page.statements:
            chunks.update(stmt.source_chunk_ids)
        return sorted(chunks)

    def build_disclaimer(self, page: WikiPage) -> str:
        """Build a disclaimer for low-confidence wiki answers."""
        synthesis_count = sum(
            1 for s in page.statements
            if (s.verified_synthesis_level or s.synthesis_level) == SynthesisLevel.SYNTHESIS.value
        )
        total = len(page.statements)
        return (
            f"This answer is based on synthesized wiki content. "
            f"{synthesis_count}/{total} statements are cross-document syntheses "
            f"without direct source verification. "
            f"Use 'nexus wiki query --rag-fallback' for source-grounded answers."
        )

    def is_degradation(self, old_level: str, new_level: str) -> bool:
        """Check if a synthesis level change is a degradation (trust decrease)."""
        return _TRUST_RANK.get(old_level, 0) > _TRUST_RANK.get(new_level, 0)

    def min_confidence(self, conf_a: str, conf_b: str) -> str:
        """Return the lower of two confidence levels."""
        order = {
            ConfidenceLevel.UNTRUSTED.value: 0,
            ConfidenceLevel.LOW.value: 1,
            ConfidenceLevel.MEDIUM.value: 2,
            ConfidenceLevel.HIGH.value: 3,
        }
        if order.get(conf_a, 0) <= order.get(conf_b, 0):
            return conf_a
        return conf_b
