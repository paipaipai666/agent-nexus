"""Wiki lint system — health checks that mark problems, don't裁决.

Three checks:
1. Consistency: contradictions between wiki pages
2. Drift: statements偏离 their canonical definitions
3. Coverage: RAG chunks not referenced by any wiki statement

All checks produce ReviewItems for the review queue.
Lint marks problems; humans裁决.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

from agentnexus.core.config import get_settings

from .models import ReviewItem, ReviewPriority, ReviewStatus
from .store import WikiStore
from .verifier import cosine_similarity

logger = logging.getLogger(__name__)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _deadline(priority: int) -> str:
    """Compute deadline based on priority SLA."""
    settings = get_settings()
    days = {
        ReviewPriority.DEFINITION_CONFLICT.value: settings.wiki_review_sla_p1_days,
        ReviewPriority.SEMANTIC_DRIFT.value: settings.wiki_review_sla_p2_days,
        ReviewPriority.COVERAGE_GAP.value: settings.wiki_review_sla_p3_days,
    }
    delta = timedelta(days=days.get(priority, 30))
    return (datetime.now(timezone.utc) + delta).replace(microsecond=0).isoformat()


class ConsistencyChecker:
    """Detect contradictions between wiki pages' canonical definitions."""

    def check(self, store: WikiStore, source_namespace: str = "") -> list[ReviewItem]:
        pages = store.list_pages(source_namespace=source_namespace)
        items: list[ReviewItem] = []

        # Collect all canonical definitions across pages
        term_pages: dict[str, list[tuple[str, str]]] = {}  # term → [(page_id, consensus)]
        for page in pages:
            for term, canon_def in page.canonical_definitions.items():
                if canon_def.consensus:
                    term_pages.setdefault(term, []).append((page.page_id, canon_def.consensus))

        # Check for contradictions between pages defining the same term
        for term, definitions in term_pages.items():
            if len(definitions) < 2:
                continue

            for i in range(len(definitions)):
                for j in range(i + 1, len(definitions)):
                    page_a, def_a = definitions[i]
                    page_b, def_b = definitions[j]

                    # Use cosine similarity to detect potential contradiction
                    sim = cosine_similarity(def_a, def_b)
                    if sim < 0.4:  # Low similarity = potential contradiction
                        items.append(ReviewItem(
                            item_id=f"consist_{uuid.uuid4().hex[:12]}",
                            priority=ReviewPriority.DEFINITION_CONFLICT.value,
                            page_id=page_a,
                            description=(
                                f"Term '{term}' has conflicting definitions across pages. "
                                f"Page '{page_a}': \"{def_a[:100]}...\" vs "
                                f"Page '{page_b}': \"{def_b[:100]}...\" "
                                f"(cosine similarity: {sim:.3f})"
                            ),
                            status=ReviewStatus.PENDING.value,
                            deadline=_deadline(ReviewPriority.DEFINITION_CONFLICT.value),
                            created_at=_utc_now(),
                        ))

        return items


class DriftDetector:
    """Detect semantic drift: statements偏离 their canonical definitions."""

    def check(self, store: WikiStore, source_namespace: str = "") -> list[ReviewItem]:
        settings = get_settings()
        drift_threshold = settings.wiki_drift_threshold
        pages = store.list_pages(source_namespace=source_namespace)
        items: list[ReviewItem] = []

        for page in pages:
            full_page = store.get_page(page.page_id, include_statements=True)
            if not full_page:
                continue
            for stmt in full_page.statements:
                if not stmt.canonical_term:
                    continue

                canon_def = page.canonical_definitions.get(stmt.canonical_term)
                if not canon_def or not canon_def.consensus:
                    continue

                # Compare statement text against canonical definition
                sim = cosine_similarity(stmt.text, canon_def.consensus)
                if sim < drift_threshold:
                    items.append(ReviewItem(
                        item_id=f"drift_{uuid.uuid4().hex[:12]}",
                        priority=ReviewPriority.SEMANTIC_DRIFT.value,
                        page_id=page.page_id,
                        statement_id=stmt.statement_id,
                        description=(
                            f"Statement drifted from canonical definition of '{stmt.canonical_term}'. "
                            f"Canonical: \"{canon_def.consensus[:100]}...\" "
                            f"Statement: \"{stmt.text[:100]}...\" "
                            f"(cosine similarity: {sim:.3f}, threshold: {drift_threshold})"
                        ),
                        status=ReviewStatus.PENDING.value,
                        deadline=_deadline(ReviewPriority.SEMANTIC_DRIFT.value),
                        created_at=_utc_now(),
                    ))

        return items


class CoverageChecker:
    """Find RAG chunks not referenced by any wiki statement."""

    def check(
        self,
        store: WikiStore,
        source_namespace: str,
        rag_namespace: str = "",
    ) -> list[ReviewItem]:
        """Check for uncovered chunks in the source RAG namespace."""
        # Get all chunks from RAG catalog
        from agentnexus.rag.store import get_knowledge_base_catalog

        catalog = get_knowledge_base_catalog()
        kb_record = catalog.get_knowledge_base(rag_namespace or source_namespace)
        if not kb_record:
            return []

        all_chunks = catalog.list_chunks_by_kb(kb_record.kb_id)
        all_chunk_ids = {c.chunk_id for c in all_chunks}

        # Get all chunk IDs referenced by wiki statements
        pages = store.list_pages(source_namespace=source_namespace)
        covered_chunks: set[str] = set()
        for page in pages:
            full_page = store.get_page(page.page_id, include_statements=True)
            if full_page:
                for stmt in full_page.statements:
                    covered_chunks.update(stmt.source_chunk_ids)

        uncovered = all_chunk_ids - covered_chunks
        if not uncovered:
            return []

        # Cap the number of coverage items to avoid flooding the queue
        max_items = 20
        items: list[ReviewItem] = []
        for chunk_id in sorted(uncovered)[:max_items]:
            items.append(ReviewItem(
                item_id=f"cover_{uuid.uuid4().hex[:12]}",
                priority=ReviewPriority.COVERAGE_GAP.value,
                page_id="",  # No specific page — it's a missing reference
                description=(
                    f"RAG chunk {chunk_id} is not referenced by any wiki statement. "
                    f"Consider ingesting the source document or linking to existing pages."
                ),
                status=ReviewStatus.PENDING.value,
                deadline=_deadline(ReviewPriority.COVERAGE_GAP.value),
                created_at=_utc_now(),
            ))

        if len(uncovered) > max_items:
            logger.info(
                f"Coverage check: {len(uncovered)} uncovered chunks, "
                f"showing first {max_items}"
            )

        return items


class WikiLinter:
    """Orchestrates all lint checks and produces review items."""

    def __init__(self, store: WikiStore):
        self.store = store
        self.consistency = ConsistencyChecker()
        self.drift = DriftDetector()
        self.coverage = CoverageChecker()

    def run_full_lint(
        self,
        source_namespace: str = "",
        rag_namespace: str = "",
    ) -> list[ReviewItem]:
        """Run all lint checks and return review items.

        Items are NOT automatically added to the review queue —
        the caller decides whether to enqueue them.
        """
        items: list[ReviewItem] = []

        logger.info("Running consistency check...")
        items.extend(self.consistency.check(self.store, source_namespace))

        logger.info("Running drift detection...")
        items.extend(self.drift.check(self.store, source_namespace))

        logger.info("Running coverage check...")
        items.extend(self.coverage.check(self.store, source_namespace, rag_namespace))

        logger.info(f"Lint complete: {len(items)} items found")
        return items

    def enqueue_items(self, items: list[ReviewItem]):
        """Add lint items to the review queue."""
        for item in items:
            self.store.add_review_item(item)

    def process_overdue_items(self) -> list[dict]:
        """Process overdue review items — auto-degrade their pages.

        Returns list of actions taken.
        """
        overdue = self.store.get_overdue_review_items()
        actions: list[dict] = []

        for item in overdue:
            if item.priority == ReviewPriority.DEFINITION_CONFLICT.value:
                # P1 overdue → mark page as untrusted
                if item.page_id:
                    self.store.update_page_confidence(
                        item.page_id, "untrusted",
                        flag=f"auto_degraded:review_timeout:{item.item_id}",
                    )
                    self.store.resolve_review_item(item.item_id, ReviewStatus.AUTO_DEGRADED.value)
                    actions.append({
                        "action": "auto_degrade",
                        "item_id": item.item_id,
                        "page_id": item.page_id,
                        "new_confidence": "untrusted",
                    })

            elif item.priority == ReviewPriority.SEMANTIC_DRIFT.value:
                # P2 overdue → revert to canonical definition
                self.store.resolve_review_item(item.item_id, ReviewStatus.AUTO_DEGRADED.value)
                actions.append({
                    "action": "revert_to_canonical",
                    "item_id": item.item_id,
                    "page_id": item.page_id,
                    "statement_id": item.statement_id,
                })

            elif item.priority == ReviewPriority.COVERAGE_GAP.value:
                # P3 overdue → archive
                self.store.resolve_review_item(item.item_id, ReviewStatus.AUTO_DEGRADED.value)
                actions.append({
                    "action": "archive",
                    "item_id": item.item_id,
                })

        return actions
