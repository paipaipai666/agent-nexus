"""Graph-based trust propagation for wiki pages.

Handles two directions:
- Degradation: when a page's confidence drops, cascade to dependents (min inheritance)
- Recovery: when a page's confidence rises, re-verify dependents (not auto-recover)

Also handles RAG → Wiki reverse triggering: when source chunks are updated,
all wiki statements referencing those chunks are re-verified.

Propagation is depth-limited (default 3) to prevent chain reactions.
"""

from __future__ import annotations

import logging

from agentnexus.core.config import get_settings

from .confidence import ConfidenceRouter
from .models import WikiStatement
from .store import WikiStore
from .verifier import MechanicalVerifier

logger = logging.getLogger(__name__)


class PropagationEngine:
    """Manages trust propagation through the wiki dependency graph."""

    def __init__(
        self,
        store: WikiStore,
        verifier: MechanicalVerifier | None = None,
        router: ConfidenceRouter | None = None,
        max_depth: int | None = None,
    ):
        self.store = store
        self.verifier = verifier or MechanicalVerifier()
        self.router = router or ConfidenceRouter()
        settings = get_settings()
        self.max_depth = max_depth or settings.wiki_propagation_max_depth

    # ── Degradation ─────────────────────────────────────────────────

    def propagate_degradation(self, page_id: str, depth: int = 0):
        """Cascade confidence degradation to dependent pages.

        Uses min inheritance: dependent confidence = min(own, source).
        """
        if depth >= self.max_depth:
            logger.debug("Max propagation depth reached at %s", page_id)
            return

        page = self.store.get_page(page_id, include_statements=False)
        if not page:
            return

        dependents = self.store.list_dependents(page_id)
        for dep_id in dependents:
            dep_page = self.store.get_page(dep_id, include_statements=False)
            if not dep_page:
                continue

            new_confidence = self.router.min_confidence(dep_page.confidence, page.confidence)
            if new_confidence != dep_page.confidence:
                logger.info(
                    f"Propagating degradation: {page_id} → {dep_id} "
                    f"({dep_page.confidence} → {new_confidence})"
                )
                self.store.update_page_confidence(
                    dep_id, new_confidence,
                    flag=f"depends_on_degraded_page:{page_id}",
                )
                # Recurse
                self.propagate_degradation(dep_id, depth + 1)

    # ── Recovery ────────────────────────────────────────────────────

    def propagate_recovery(self, page_id: str, depth: int = 0):
        """Cascade recovery: re-verify dependent pages (don't auto-recover).

        Unlike degradation, recovery requires re-verification — the dependent
        page might have its own issues accumulated while degraded.
        """
        if depth >= self.max_depth:
            return

        dependents = self.store.list_dependents(page_id)
        for dep_id in dependents:
            self._reverify_page(dep_id)
            # Recurse
            self.propagate_recovery(dep_id, depth + 1)

    def _reverify_page(self, page_id: str):
        """Re-verify all statements in a page and recompute confidence."""
        page = self.store.get_page(page_id)
        if not page:
            return

        changed = False
        for stmt in page.statements:
            chunk_texts = self._get_chunk_texts(stmt)
            if not chunk_texts:
                continue

            new_level, did_change = self.verifier.verify_and_update_statement(stmt, chunk_texts)
            if did_change:
                self.store.update_statement_synthesis_level(stmt.statement_id, new_level)
                changed = True

        if changed:
            # Recompute page confidence
            updated_page = self.store.get_page(page_id)
            if updated_page:
                new_conf = self.router.compute_page_confidence(updated_page)
                if new_conf != updated_page.confidence:
                    self.store.update_page_confidence(page_id, new_conf)
                    logger.info("Page %s confidence updated to %s after re-verification", page_id, new_conf)

    # ── RAG → Wiki Reverse Trigger ──────────────────────────────────

    def on_chunk_update(self, chunk_ids: list[str]):
        """When RAG chunks are updated, re-verify all wiki statements that reference them.

        This is the reverse trigger: RAG layer → Wiki layer.
        """
        if not chunk_ids:
            return

        affected_statements = self.store.find_statements_by_chunks(chunk_ids)
        logger.info("Chunk update: %d chunks, %d affected statements", len(chunk_ids), len(affected_statements))

        affected_pages: set[str] = set()
        for stmt in affected_statements:
            chunk_texts = self._get_chunk_texts(stmt)
            if not chunk_texts:
                continue

            old_level = stmt.verified_synthesis_level or stmt.synthesis_level
            new_level = self.verifier.verify_statement(stmt, chunk_texts)

            if new_level != old_level:
                logger.info(
                    f"Statement {stmt.statement_id}: {old_level} → {new_level} "
                    f"after chunk update"
                )
                self.store.update_statement_synthesis_level(stmt.statement_id, new_level)
                affected_pages.add(stmt.page_id)

                # Propagate based on direction
                if self.router.is_degradation(old_level, new_level):
                    self.propagate_degradation(stmt.page_id)
                else:
                    self.propagate_recovery(stmt.page_id)

        # Recompute confidence for directly affected pages
        for page_id in affected_pages:
            page = self.store.get_page(page_id)
            if page:
                new_conf = self.router.compute_page_confidence(page)
                if new_conf != page.confidence:
                    self.store.update_page_confidence(page_id, new_conf)

    # ── Helper ──────────────────────────────────────────────────────

    def _get_chunk_texts(self, stmt: WikiStatement) -> dict[str, str]:
        """Fetch chunk texts for a statement's source chunk IDs.

        Returns dict of chunk_id → text. Falls back to empty if chunks not found.
        """
        if not stmt.source_chunk_ids:
            return {}

        # Import here to avoid circular dependency at module level
        from agentnexus.rag.store import get_knowledge_base_catalog

        catalog = get_knowledge_base_catalog()
        result: dict[str, str] = {}
        for chunk_id in stmt.source_chunk_ids:
            # Try to find the chunk in the RAG catalog
            # We need to search across all documents — use a direct query
            try:
                rows = catalog._conn.execute(
                    "SELECT text FROM document_chunks WHERE chunk_id = ?",
                    (chunk_id,),
                ).fetchall()
                if rows:
                    result[chunk_id] = rows[0]["text"]
            except Exception as e:
                logger.warning("Failed to fetch chunk %s: %s", chunk_id, e)
        return result
