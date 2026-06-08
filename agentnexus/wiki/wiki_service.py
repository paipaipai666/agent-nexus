"""Wiki service — main orchestration layer.

Ties together: ingestion, verification, confidence routing, lint, and RAG integration.
This is the primary entry point for wiki operations.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from agentnexus.core.config import get_settings
from agentnexus.rag import embeddings as embedding_service
from agentnexus.rag.store import get_knowledge_base_catalog

from .calibration import CalibrationSample, run_calibration
from .confidence import ConfidenceRouter, QueryRouteResult
from .lint import WikiLinter
from .models import (
    CanonicalDefinition,
    ConfidenceLevel,
    DefinitionEntry,
    QueryDecision,
    SynthesisLevel,
    WikiPage,
    WikiStatement,
)
from .propagation import PropagationEngine
from .store import WikiStore, get_wiki_store
from .verifier import MechanicalVerifier

logger = logging.getLogger(__name__)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _make_id(prefix: str = "wiki") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


@dataclass
class WikiQueryResult:
    """Result of a wiki-aware query."""

    used_wiki: bool = False
    decision: str = ""
    confidence: str = ""
    answer: str = ""
    source_chunks: list[str] = field(default_factory=list)
    disclaimer: str = ""
    rag_results: list[dict] = field(default_factory=list)


class WikiService:
    """Main service for hybrid Wiki + RAG operations."""

    def __init__(
        self,
        store: WikiStore | None = None,
        verifier: MechanicalVerifier | None = None,
        router: ConfidenceRouter | None = None,
    ):
        self.store = store or get_wiki_store()
        self.verifier = verifier or MechanicalVerifier()
        self.router = router or ConfidenceRouter()
        self.propagation = PropagationEngine(self.store, self.verifier, self.router)
        self.linter = WikiLinter(self.store)

    # ── Ingestion ───────────────────────────────────────────────────

    def ingest_source(
        self,
        source_text: str,
        source_uri: str,
        source_namespace: str,
        page_type: str = "concept",
        llm_client=None,
    ) -> WikiPage:
        """Ingest a source document into the wiki.

        This is the "compile" step: LLM reads the source and generates
        wiki pages with statements and canonical definitions.

        Args:
            source_text: Raw text content of the source document.
            source_uri: URI/path of the source document.
            source_namespace: Which RAG namespace this source belongs to.
            page_type: Type of wiki page to generate.
            llm_client: Optional LLM client for page generation.

        Returns:
            The generated WikiPage.
        """
        # Generate wiki page from source using LLM
        page = self._generate_wiki_page(
            source_text, source_uri, source_namespace, page_type, llm_client
        )

        # Verify all statements mechanically
        self._verify_page_statements(page)

        # Compute confidence
        page.confidence = self.router.compute_page_confidence(page)

        # Store page
        self.store.upsert_page(page)
        for stmt in page.statements:
            self.store.upsert_statement(stmt)
        for term, canon_def in page.canonical_definitions.items():
            self.store.upsert_canonical_definition(page.page_id, term, canon_def)

        # Index in ChromaDB for wiki search
        self._index_page_in_chroma(page)

        logger.info(
            f"Ingested source '{source_uri}' → page '{page.title}' "
            f"({len(page.statements)} statements, confidence={page.confidence})"
        )
        return page

    def ingest_source_with_context(
        self,
        source_text: str,
        source_uri: str,
        source_namespace: str,
        existing_pages: list[WikiPage],
        llm_client=None,
    ) -> WikiPage:
        """Ingest a source with awareness of existing wiki pages.

        This enables cross-referencing: the LLM sees existing pages and
        can link new statements to existing concepts.
        """
        page = self._generate_wiki_page_with_context(
            source_text, source_uri, source_namespace, existing_pages, llm_client
        )

        # Verify and store
        self._verify_page_statements(page)
        page.confidence = self.router.compute_page_confidence(page)
        self.store.upsert_page(page)
        for stmt in page.statements:
            self.store.upsert_statement(stmt)
        for term, canon_def in page.canonical_definitions.items():
            self.store.upsert_canonical_definition(page.page_id, term, canon_def)

        # Build dependency links
        for ref_page_id in self._extract_cross_references(page, existing_pages):
            self.store.add_dependency(ref_page_id, page.page_id)

        self._index_page_in_chroma(page)
        return page

    # ── Query ───────────────────────────────────────────────────────

    def query(
        self,
        question: str,
        source_namespace: str,
        rag_namespace: str = "",
        force_rag: bool = False,
    ) -> WikiQueryResult:
        """Wiki-aware query with confidence-based routing.

        1. Search wiki for relevant pages
        2. Compute confidence for best page
        3. Route based on confidence (wiki / wiki+sources / wiki+disclaimer / RAG fallback)
        """
        if force_rag:
            return self._rag_fallback(question, rag_namespace or source_namespace)

        # Search wiki
        wiki_pages = self.search_wiki_pages(question, source_namespace)
        if not wiki_pages:
            return self._rag_fallback(question, rag_namespace or source_namespace)

        best_page = wiki_pages[0]
        decision = self.router.route(best_page)

        if decision == QueryDecision.FALLBACK_TO_RAG:
            return self._rag_fallback(question, rag_namespace or source_namespace)

        result = WikiQueryResult(
            used_wiki=True,
            decision=decision.value,
            confidence=best_page.confidence,
            answer=best_page.content,
        )

        if decision in (QueryDecision.USE_WIKI_WITH_SOURCES, QueryDecision.USE_WIKI_WITH_DISCLAIMER):
            result.source_chunks = self.router.get_source_chunks(best_page)

        if decision == QueryDecision.USE_WIKI_WITH_DISCLAIMER:
            result.disclaimer = self.router.build_disclaimer(best_page)

        return result

    def search_wiki_pages(self, query: str, source_namespace: str, limit: int = 5) -> list[WikiPage]:
        """Search wiki pages by semantic similarity."""
        settings = get_settings()
        wiki_namespace = settings.wiki_namespace

        from agentnexus.storage.chroma import search as chroma_search

        results = chroma_search(query, limit=limit, namespace=wiki_namespace)
        if not results:
            return []

        # Fetch full pages from store
        pages: list[WikiPage] = []
        for r in results:
            page_id = r.get("metadata", {}).get("page_id", "")
            if page_id:
                page = self.store.get_page(page_id)
                if page and page.source_namespace == source_namespace:
                    pages.append(page)
        return pages

    # ── Lint ────────────────────────────────────────────────────────

    def run_lint(self, source_namespace: str = "", rag_namespace: str = "") -> list[dict]:
        """Run full lint and enqueue review items."""
        items = self.linter.run_full_lint(source_namespace, rag_namespace)
        self.linter.enqueue_items(items)
        return [
            {
                "item_id": item.item_id,
                "priority": item.priority,
                "page_id": item.page_id,
                "description": item.description,
            }
            for item in items
        ]

    def process_overdue_reviews(self) -> list[dict]:
        """Process overdue review items (auto-degradation)."""
        return self.linter.process_overdue_items()

    # ── Calibration ─────────────────────────────────────────────────

    def calibrate(self, samples: list[CalibrationSample]) -> dict:
        """Run threshold calibration with human-labeled samples."""
        result = run_calibration(self.store, samples)

        # Reload verifier with new thresholds
        self.verifier = MechanicalVerifier(thresholds=result["thresholds"])
        self.propagation.verifier = self.verifier

        return result

    def check_calibration_needed(self, source_namespace: str = "") -> bool:
        """Check if wiki has grown enough to warrant re-calibration."""
        settings = get_settings()
        stats = self.store.get_stats(source_namespace)
        calibration = self.store.get_latest_calibration()

        if not calibration:
            return stats["page_count"] > 0  # Need calibration if wiki has pages

        calibrated_size = calibration.get("sample_size", 0)
        current_size = stats["page_count"]
        if calibrated_size == 0:
            return True

        growth_pct = (current_size - calibrated_size) / calibrated_size
        return growth_pct >= settings.wiki_calibration_retrigger_pct

    # ── RAG Integration ─────────────────────────────────────────────

    def on_rag_ingest(self, chunk_ids: list[str]):
        """Hook: called after RAG ingestion to trigger wiki re-verification."""
        settings = get_settings()
        if not settings.wiki_enabled:
            return
        self.propagation.on_chunk_update(chunk_ids)

    # ── Stats ───────────────────────────────────────────────────────

    def get_stats(self, source_namespace: str = "") -> dict:
        stats = self.store.get_stats(source_namespace)
        stats["calibration_needed"] = self.check_calibration_needed(source_namespace)
        return stats

    # ── Internal ────────────────────────────────────────────────────

    def _verify_page_statements(self, page: WikiPage):
        """Verify all statements in a page using mechanical verifier."""
        for stmt in page.statements:
            chunk_texts = self.propagation._get_chunk_texts(stmt)
            if chunk_texts:
                new_level, _ = self.verifier.verify_and_update_statement(stmt, chunk_texts)
                stmt.verified_synthesis_level = new_level
            else:
                # No chunk texts available — mark as unverified
                stmt.verified_synthesis_level = stmt.synthesis_level

    def _generate_wiki_page(
        self,
        source_text: str,
        source_uri: str,
        source_namespace: str,
        page_type: str,
        llm_client,
    ) -> WikiPage:
        """Generate a wiki page from source text using LLM.

        This is a placeholder — the actual LLM call depends on the
        configured LLM client. The prompt should instruct the LLM to:
        1. Extract key concepts and entities
        2. Create statements with synthesis_level annotations
        3. Define canonical definitions for key terms
        4. Link to existing concepts where relevant
        """
        # For now, create a minimal page structure
        # In production, this would call the LLM with a structured prompt
        page = WikiPage(
            page_id=_make_id("page"),
            title=source_uri.split("/")[-1] if "/" in source_uri else source_uri,
            page_type=page_type,
            content=source_text[:2000],  # Placeholder: truncated source
            source_namespace=source_namespace,
            created_at=_utc_now(),
            updated_at=_utc_now(),
        )
        return page

    def _generate_wiki_page_with_context(
        self,
        source_text: str,
        source_uri: str,
        source_namespace: str,
        existing_pages: list[WikiPage],
        llm_client,
    ) -> WikiPage:
        """Generate wiki page with awareness of existing pages."""
        # Context-aware generation: include existing page titles/summaries
        context = "\n".join(
            f"- {p.title}: {p.content[:200]}..." for p in existing_pages[:20]
        )
        # LLM prompt would include this context
        return self._generate_wiki_page(source_text, source_uri, source_namespace, "concept", llm_client)

    def _extract_cross_references(self, page: WikiPage, existing_pages: list[WikiPage]) -> list[str]:
        """Extract cross-references between new page and existing pages."""
        # Simple keyword matching for now
        ref_ids: list[str] = []
        page_terms = set(page.canonical_definitions.keys())
        for existing in existing_pages:
            existing_terms = set(existing.canonical_definitions.keys())
            if page_terms & existing_terms:
                ref_ids.append(existing.page_id)
        return ref_ids

    def _index_page_in_chroma(self, page: WikiPage):
        """Index wiki page in ChromaDB for semantic search."""
        settings = get_settings()
        wiki_namespace = settings.wiki_namespace

        from agentnexus.storage.chroma import upsert_documents

        # Index page content + title
        text = f"{page.title}\n\n{page.content}"
        metadata = {
            "page_id": page.page_id,
            "page_type": page.page_type,
            "confidence": page.confidence,
            "source_namespace": page.source_namespace,
        }
        upsert_documents(
            texts=[text],
            metadatas=[metadata],
            ids=[page.page_id],
            namespace=wiki_namespace,
        )

    def _rag_fallback(self, question: str, namespace: str) -> WikiQueryResult:
        """Fall back to pure RAG search."""
        from agentnexus.rag.kb_service import search_kb

        results = search_kb(question, namespace=namespace, top_k=5, view="default")
        return WikiQueryResult(
            used_wiki=False,
            decision=QueryDecision.FALLBACK_TO_RAG.value,
            rag_results=results,
        )
