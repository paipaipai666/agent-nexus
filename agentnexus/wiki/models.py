"""Data models for the hybrid Wiki system."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class SynthesisLevel(str, Enum):
    """How a wiki statement relates to its source chunks.

    direct_quote:    High Jaccard overlap with a single source chunk.
    paraphrase:      High cosine similarity with a single source chunk.
    cross_reference: Multiple source chunks, each verified relevant.
    synthesis:       No single source; cross-document conclusion.
    """

    DIRECT_QUOTE = "direct_quote"
    PARAPHRASE = "paraphrase"
    CROSS_REFERENCE = "cross_reference"
    SYNTHESIS = "synthesis"


class ConfidenceLevel(str, Enum):
    """Page-level or statement-level confidence for query routing."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNTRUSTED = "untrusted"


class QueryDecision(str, Enum):
    """What the confidence router decides for a query."""

    USE_WIKI = "use_wiki"
    USE_WIKI_WITH_SOURCES = "use_wiki_with_sources"
    USE_WIKI_WITH_DISCLAIMER = "use_wiki_with_disclaimer"
    FALLBACK_TO_RAG = "fallback_to_rag"


class ReviewPriority(int, Enum):
    """Review queue priority levels."""

    DEFINITION_CONFLICT = 1
    SEMANTIC_DRIFT = 2
    COVERAGE_GAP = 3


class ReviewStatus(str, Enum):
    """Review item lifecycle status."""

    PENDING = "pending"
    RESOLVED = "resolved"
    AUTO_DEGRADED = "auto_degraded"


@dataclass(slots=True)
class DefinitionEntry:
    """A single definition of a term from one source chunk."""

    text: str
    source_chunk_id: str
    confidence: float  # 0.0-1.0, based on source quality


@dataclass(slots=True)
class CanonicalDefinition:
    """Multi-source definition of a concept.

    consensus is None when divergence >= 0.2 — no authoritative summary.
    """

    definitions: list[DefinitionEntry] = field(default_factory=list)
    consensus: str | None = None
    divergence: float = 0.0
    last_recalculated: str = ""


@dataclass(slots=True)
class WikiStatement:
    """A single claim/assertion within a wiki page.

    source_chunk_ids: RAG chunk IDs that contributed to this statement.
    synthesis_level: LLM-assigned level (pre-verification).
    verified_synthesis_level: Level after mechanical verification (None = not yet verified).
    """

    statement_id: str = ""
    page_id: str = ""
    text: str = ""
    synthesis_level: str = SynthesisLevel.SYNTHESIS.value
    source_chunk_ids: list[str] = field(default_factory=list)
    canonical_term: str | None = None
    verified_synthesis_level: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""


@dataclass(slots=True)
class WikiPage:
    """A wiki page containing statements and canonical definitions.

    confidence: Computed from statement synthesis levels via rule-tree.
    flags: Propagation markers, e.g. ["depends_on_degraded_page:xxx"].
    source_namespace: Which RAG namespace this wiki is bound to.
    """

    page_id: str = ""
    title: str = ""
    page_type: str = "concept"  # entity | concept | overview | source_summary
    content: str = ""
    statements: list[WikiStatement] = field(default_factory=list)
    canonical_definitions: dict[str, CanonicalDefinition] = field(default_factory=dict)
    confidence: str = ConfidenceLevel.HIGH.value
    flags: list[str] = field(default_factory=list)
    source_namespace: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""


@dataclass(slots=True)
class ReviewItem:
    """An item in the review queue.

    priority: 1=definition conflict, 2=drift, 3=coverage.
    deadline: Auto-degradation happens when current time exceeds this.
    """

    item_id: str = ""
    priority: int = ReviewPriority.COVERAGE_GAP.value
    page_id: str = ""
    statement_id: str | None = None
    description: str = ""
    status: str = ReviewStatus.PENDING.value
    deadline: str = ""
    resolved_at: str | None = None
    created_at: str = ""
