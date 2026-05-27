"""Main SkillRouter orchestrator and decision logic."""

from __future__ import annotations

from typing import Any

from agentnexus.skills.registry import SkillEntry
from agentnexus.skills.router.llm_fallback import route_with_llm
from agentnexus.skills.router.normalize import tokenize
from agentnexus.skills.router.parse import extract_intent_signals
from agentnexus.skills.router.rank import (
    best_candidate_is_intent_confident,
    cosine_similarity,
    rerank_with_intent,
    score_example_similarity,
    score_fuzzy_match,
    score_indexed_entry,
    score_metadata_alignment,
)
from agentnexus.skills.router.retrieve import build_index, entries_signature
from agentnexus.skills.router.types import (
    IndexedSkillMetadata,
    IntentSignals,
    SkillRoute,
    SkillRouteDecision,
    SkillRouterIndex,
)


def format_reason(entry: SkillEntry, matched: list[str], score: float) -> str:
    terms = ", ".join(matched[:8]) or "-"
    return f"{entry.qualified_id} matched metadata terms: {terms} (score={score:.1f})"


class SkillRouter:
    """Route a user request to a skill using metadata, fuzzy matching,
    intent extraction, and semantic similarity."""

    def __init__(
        self,
        *,
        min_score: float = 2.0,
        margin: float = 0.75,
        max_terms: int = 8,
        use_embeddings: bool = True,
        keyword_weight: float = 0.6,
        semantic_weight: float = 0.4,
        semantic_threshold: float = 0.3,
    ):
        self.min_score = min_score
        self.margin = margin
        self.max_terms = max_terms
        self.use_embeddings = use_embeddings
        self.keyword_weight = keyword_weight
        self.semantic_weight = semantic_weight
        self.semantic_threshold = semantic_threshold
        self.index = SkillRouterIndex.build([], compute_embeddings=False)
        self._query_cache: dict[str, list[float]] = {}

    def rebuild(self, entries: list[SkillEntry]) -> None:
        self.index = build_index(
            entries,
            compute_embeddings=self.use_embeddings,
        )
        self._query_cache.clear()

    def route(self, text: str, entries: list[SkillEntry]) -> SkillRoute | None:
        return self.decide(text, entries).route

    def decide(self, text: str, entries: list[SkillEntry]) -> SkillRouteDecision:
        signature = entries_signature(entries)
        if signature != self.index.signature:
            self.rebuild(entries)
        return self.decide_indexed(text)

    def decide_indexed(self, text: str) -> SkillRouteDecision:
        query_terms = set(tokenize(text))
        if not query_terms:
            return SkillRouteDecision(None, (), False, "no query terms")

        query_embedding = (
            self._get_query_embedding(text) if self.use_embeddings else None
        )
        intent = extract_intent_signals(text, query_terms)

        scored: list[SkillRoute] = []
        for item in self.index.items:
            # ── Exact keyword match ──
            matched = sorted(query_terms & item.terms)
            keyword_score = score_indexed_entry(
                query_terms, item, matched, self.index.idf,
            )

            # ── Structured metadata match ──
            meta_score = score_metadata_alignment(
                query_terms, item, self.index.idf, fuzzy=False, intent=intent,
            )

            # ── Fuzzy keyword match (only if no exact hit) ──
            fuzzy_matched: list[str] = []
            fuzzy_score = 0.0
            if not matched:
                fuzzy_matched, fuzzy_score = score_fuzzy_match(
                    query_terms, item, self.index.idf,
                )

            # ── Example similarity (direct query-to-example comparison) ──
            example_matched, example_score = score_example_similarity(
                query_terms, item, self.index.idf,
            )

            all_matched = matched + fuzzy_matched + example_matched

            # ── Semantic match ──
            semantic_score = 0.0
            if query_embedding and item.embedding:
                semantic_score = cosine_similarity(query_embedding, item.embedding)

            # ── Hybrid scoring ──
            base_score = keyword_score + meta_score + fuzzy_score + example_score
            has_signal = (
                base_score > 0 or semantic_score > self.semantic_threshold
            )
            if has_signal:
                combined_score = self._combine_scores(base_score, semantic_score)
                enhanced = self._enhance_matched_terms(
                    all_matched, query_terms, item, semantic_score,
                )
                scored.append(SkillRoute(
                    entry=item.entry,
                    score=combined_score,
                    matched_terms=tuple(enhanced[: self.max_terms]),
                    reason=format_reason(item.entry, enhanced, combined_score),
                ))

        if not scored:
            return SkillRouteDecision(
                None, (), False, "no metadata matches", mode="abstain",
            )

        scored.sort(key=lambda r: r.score, reverse=True)

        # ── Rule-based rerank with intent signals ──
        if len(scored) > 1:
            scored = rerank_with_intent(scored, intent, self.index)

        best = scored[0]
        candidates = tuple(scored[:5])

        # ── Confidence calibration ──
        confidence = self._compute_confidence(scored, intent)

        # ── Multi-intent detection ──
        secondary = ()
        if intent.connectors and len(scored) > 1:
            secondary = tuple(
                r.entry.qualified_id for r in scored[1:3]
                if r.score > self.min_score * 0.5
            )

        # ── Decision gate ──
        if best.score < self.min_score:
            return SkillRouteDecision(
                None, candidates, bool(candidates),
                "below deterministic threshold",
                mode="abstain",
                confidence=confidence,
            )
        if len(scored) > 1 and best.score - scored[1].score < self.margin:
            if best_candidate_is_intent_confident(
                best, scored[1], intent, self.index,
            ):
                mode = "multi_intent" if secondary else "single"
                return SkillRouteDecision(
                    best, candidates, False, best.reason,
                    mode=mode, confidence=confidence,
                    secondary_skills=secondary,
                )
            return SkillRouteDecision(
                None, candidates, True, "candidate scores are too close",
                mode="ambiguous", confidence=confidence,
            )
        mode = "multi_intent" if secondary else "single"
        return SkillRouteDecision(
            best, candidates, False, best.reason,
            mode=mode, confidence=confidence,
            secondary_skills=secondary,
        )

    def _compute_confidence(
        self,
        scored: list[SkillRoute],
        intent: IntentSignals,
    ) -> float:
        """Compute routing confidence in [0, 1]."""
        if not scored:
            return 0.0
        best = scored[0]
        if best.score <= 0:
            return 0.0
        # Base confidence from score magnitude
        base = min(best.score / (self.min_score * 3), 1.0)
        # Margin bonus: bigger gap -> more confident
        if len(scored) > 1:
            margin = best.score - scored[1].score
            margin_bonus = min(margin / (self.margin * 2), 0.3)
        else:
            margin_bonus = 0.3
        # Intent alignment bonus
        intent_bonus = 0.0
        if intent.primary_action or intent.primary_object:
            intent_bonus = 0.1
        return min(base * 0.6 + margin_bonus + intent_bonus, 1.0)

    def _get_query_embedding(self, text: str) -> list[float] | None:
        """Get or compute query embedding with caching."""
        if not text:
            return None
        cache_key = text.strip().lower()
        if cache_key in self._query_cache:
            return self._query_cache[cache_key]
        try:
            from agentnexus.rag.embeddings import embed_texts

            embedding = embed_texts([text])[0]
            self._query_cache[cache_key] = embedding
            if len(self._query_cache) > 1000:
                keys_to_remove = list(self._query_cache.keys())[:500]
                for key in keys_to_remove:
                    del self._query_cache[key]
            return embedding
        except Exception:
            return None

    def _combine_scores(self, keyword_score: float, semantic_score: float) -> float:
        if keyword_score <= 0 and semantic_score <= 0:
            return 0.0
        normalized_keyword = min(keyword_score / 10.0, 1.0)
        combined = (
            self.keyword_weight * normalized_keyword
            + self.semantic_weight * semantic_score
        )
        return combined * 10.0

    def _enhance_matched_terms(
        self,
        matched: list[str],
        query_terms: set[str],
        item: IndexedSkillMetadata,
        semantic_score: float,
    ) -> list[str]:
        enhanced = list(matched)
        if not matched and semantic_score > self.semantic_threshold:
            enhanced.append(f"semantic_match({semantic_score:.2f})")
        return enhanced

    def route_with_llm(
        self,
        text: str,
        entries: list[SkillEntry],
        llm_client: Any = None,
    ) -> SkillRoute | None:
        decision = self.decide(text, entries)
        if decision.route is not None or not decision.uncertain or llm_client is None:
            return decision.route
        return route_with_llm(
            text, decision.candidates, decision.reason, llm_client,
        )
