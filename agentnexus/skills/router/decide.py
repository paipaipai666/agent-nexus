"""SkillRecommender — ranks skills by relevance without making routing decisions.

The recommender scores and ranks candidate skills based on metadata alignment,
fuzzy matching, intent extraction, and semantic similarity. It does NOT decide
whether to activate a skill — that decision is made by the LLM with access to
conversation context and user preferences.
"""

from __future__ import annotations

from typing import Any

from agentnexus.skills.registry import SkillEntry
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


class SkillRecommender:
    """Rank skills by relevance to a user query.

    This class only recommends — it does not decide whether to activate
    a skill. The LLM makes that decision with full context.
    """

    def __init__(
        self,
        *,
        min_score: float = 2.0,
        max_terms: int = 8,
        max_candidates: int = 5,
        use_embeddings: bool = True,
        keyword_weight: float = 0.6,
        semantic_weight: float = 0.4,
        semantic_threshold: float = 0.3,
    ):
        self.min_score = min_score
        self.max_terms = max_terms
        self.max_candidates = max_candidates
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

    def rank(
        self, text: str, entries: list[SkillEntry] | None = None,
    ) -> list[SkillRoute]:
        """Return top-K candidate skills ranked by relevance.

        Does NOT make routing decisions — only recommends.
        Returns an empty list if no skills match.
        """
        if entries is not None:
            signature = entries_signature(entries)
            if signature != self.index.signature:
                self.rebuild(entries)
        return self._rank_indexed(text)

    def _rank_indexed(self, text: str) -> list[SkillRoute]:
        """Score all indexed skills and return top-K sorted by score."""
        query_terms = set(tokenize(text))
        # Filter out single-char non-CJK tokens (punctuation noise)
        meaningful = {
            t for t in query_terms
            if len(t) >= 2 or ("一" <= t <= "鿿") or ("가" <= t <= "힯")
        }
        if not meaningful:
            return []

        query_embedding = (
            self._get_query_embedding(text) if self.use_embeddings else None
        )
        intent = extract_intent_signals(text, meaningful)

        scored: list[SkillRoute] = []
        for item in self.index.items:
            # ── Exact keyword match ──
            matched = sorted(meaningful & item.terms)
            keyword_score = score_indexed_entry(
                meaningful, item, matched, self.index.idf,
            )

            # ── Structured metadata match ──
            meta_score = score_metadata_alignment(
                meaningful, item, self.index.idf, fuzzy=False, intent=intent,
            )

            # ── Fuzzy keyword match (only if no exact hit) ──
            fuzzy_matched: list[str] = []
            fuzzy_score = 0.0
            if not matched:
                fuzzy_matched, fuzzy_score = score_fuzzy_match(
                    meaningful, item, self.index.idf,
                )

            # ── Example similarity ──
            example_matched, example_score = score_example_similarity(
                meaningful, item, self.index.idf,
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
                    all_matched, meaningful, item, semantic_score,
                )
                scored.append(SkillRoute(
                    entry=item.entry,
                    score=combined_score,
                    matched_terms=tuple(enhanced[: self.max_terms]),
                    reason=format_reason(item.entry, enhanced, combined_score),
                ))

        if not scored:
            return []

        scored.sort(key=lambda r: r.score, reverse=True)

        # ── Rerank with intent signals ──
        if len(scored) > 1:
            scored = rerank_with_intent(scored, intent, self.index)

        # Return only candidates above minimum score threshold
        return [r for r in scored[:self.max_candidates] if r.score >= self.min_score]

    def _compute_confidence(
        self,
        scored: list[SkillRoute],
        intent: IntentSignals,
    ) -> float:
        """Compute confidence in [0, 1] for the top candidate."""
        if not scored:
            return 0.0
        best = scored[0]
        if best.score <= 0:
            return 0.0
        base = min(best.score / (self.min_score * 3), 1.0)
        if len(scored) > 1:
            margin = best.score - scored[1].score
            margin_bonus = min(margin / 1.5, 0.3)
        else:
            margin_bonus = 0.3
        intent_bonus = 0.0
        if intent.primary_action or intent.primary_object:
            intent_bonus = 0.1
        return min(base * 0.6 + margin_bonus + intent_bonus, 1.0)

    def _get_query_embedding(self, text: str) -> list[float] | None:
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

    # ── Backward-compatible methods (for tests and legacy callers) ──

    def decide(self, text: str, entries: list[SkillEntry]) -> SkillRouteDecision:
        """Backward-compat: return a SkillRouteDecision from ranking."""
        candidates = self.rank(text, entries)
        if not candidates:
            return SkillRouteDecision(None, (), False, "no candidates", mode="abstain")
        best = candidates[0]
        intent = extract_intent_signals(text, set(tokenize(text)))
        confidence = self._compute_confidence(candidates, intent)
        # Detect ambiguity: top candidates too close
        if len(candidates) > 1 and best.score - candidates[1].score < self.min_score * 0.375:
            # Use intent to disambiguate
            if best_candidate_is_intent_confident(
                best, candidates[1], intent, self.index,
            ):
                return SkillRouteDecision(
                    best, tuple(candidates), False, best.reason,
                    mode="single", confidence=confidence,
                )
            return SkillRouteDecision(
                None, tuple(candidates), True, "candidate scores are too close",
                mode="ambiguous", confidence=confidence,
            )
        return SkillRouteDecision(
            best, tuple(candidates), False, best.reason,
            mode="single", confidence=confidence,
        )

    def decide_indexed(self, text: str) -> SkillRouteDecision:
        """Backward-compat: decide using the current index."""
        return self.decide(text, None)

    def route(self, text: str, entries: list[SkillEntry]) -> SkillRoute | None:
        """Backward-compat: return top candidate or None."""
        candidates = self.rank(text, entries)
        return candidates[0] if candidates else None

    def route_with_llm(
        self,
        text: str,
        entries: list[SkillEntry],
        llm_client: Any = None,
    ) -> SkillRoute | None:
        """Backward-compat: rank, return directly if confident, else LLM fallback."""
        candidates = self.rank(text, entries)
        if not candidates:
            return None
        # If only one candidate or clear winner, return directly
        if len(candidates) == 1:
            return candidates[0]
        margin = candidates[0].score - candidates[1].score
        if margin >= self.min_score * 0.375:
            return candidates[0]
        # Close scores — check if intent disambiguates
        intent = extract_intent_signals(text, set(tokenize(text)))
        if best_candidate_is_intent_confident(
            candidates[0], candidates[1], intent, self.index,
        ):
            return candidates[0]
        # Ambiguous — use LLM if available, else return None
        if llm_client is None:
            return None
        from agentnexus.skills.router.llm_fallback import route_with_llm as _rwl
        return _rwl(text, tuple(candidates), "recommender top candidates", llm_client)


# Backward-compatible alias
SkillRouter = SkillRecommender
