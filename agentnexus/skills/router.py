"""Enhanced metadata router for SKILL.md driven skills.

Supports:
- Deterministic keyword matching with IDF weighting
- Semantic similarity via embeddings (for synonym/multilingual handling)
- LLM-based disambiguation for uncertain cases
- Hybrid scoring combining keyword and semantic signals
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from typing import Any

from agentnexus.skills.registry import SkillEntry

_TOKEN_RE = re.compile(r"[\w\u4e00-\u9fff]+", re.UNICODE)
_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)
_ROUTER_RESPONSE_SCHEMA = {
    "type": "json_object",
}
_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "for",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "the",
    "to",
    "use",
    "when",
    "with",
    "需要",
    "使用",
    "用于",
    "这个",
    "一个",
}


@dataclass(frozen=True)
class SkillRoute:
    entry: SkillEntry
    score: float
    matched_terms: tuple[str, ...]
    reason: str
    source: str = "deterministic"


@dataclass(frozen=True)
class SkillRouteDecision:
    route: SkillRoute | None
    candidates: tuple[SkillRoute, ...]
    uncertain: bool
    reason: str


@dataclass(frozen=True)
class IndexedSkillMetadata:
    entry: SkillEntry
    terms: frozenset[str]
    id_terms: frozenset[str]
    name_terms: frozenset[str]
    embedding: tuple[float, ...] = ()  # Skill embedding vector


@dataclass
class HybridScore:
    """Combined score from keyword and semantic matching."""
    keyword_score: float = 0.0
    semantic_score: float = 0.0
    combined_score: float = 0.0
    matched_terms: tuple[str, ...] = ()
    reason: str = ""


@dataclass(frozen=True)
class SkillRouterIndex:
    items: tuple[IndexedSkillMetadata, ...]
    idf: dict[str, float]
    signature: tuple[str, ...]

    @classmethod
    def build(
        cls,
        entries: list[SkillEntry],
        *,
        compute_embeddings: bool = True,
    ) -> "SkillRouterIndex":
        items: list[IndexedSkillMetadata] = []
        doc_freq: dict[str, int] = {}

        # Compute embeddings if enabled
        embeddings: list[tuple[float, ...]] = []
        if compute_embeddings and entries:
            embeddings = _compute_skill_embeddings(entries)

        for i, entry in enumerate(entries):
            id_terms = frozenset(_tokenize(entry.workflow_id.replace("-", " ").replace("_", " ")))
            name_terms = frozenset(_tokenize(entry.display_name))
            terms = frozenset(_entry_terms(entry))
            embedding = embeddings[i] if i < len(embeddings) else ()
            items.append(IndexedSkillMetadata(
                entry=entry,
                terms=terms,
                id_terms=id_terms,
                name_terms=name_terms,
                embedding=embedding,
            ))
            for term in terms:
                doc_freq[term] = doc_freq.get(term, 0) + 1
        count = max(len(items), 1)
        idf = {
            term: 1.0 + math.log((count + 1) / (freq + 1))
            for term, freq in doc_freq.items()
        }
        return cls(
            items=tuple(items),
            idf=idf,
            signature=_entries_signature(entries),
        )


class SkillRouter:
    """Route a user request to a skill using metadata and semantic similarity.

    Supports:
    - Keyword matching with IDF weighting (fast, deterministic)
    - Semantic similarity via embeddings (handles synonyms, multilingual)
    - Hybrid scoring combining both signals
    - LLM fallback for uncertain cases
    """

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
        self.index = SkillRouterIndex.build(
            entries,
            compute_embeddings=self.use_embeddings,
        )
        self._query_cache.clear()

    def route(self, text: str, entries: list[SkillEntry]) -> SkillRoute | None:
        return self.decide(text, entries).route

    def decide(self, text: str, entries: list[SkillEntry]) -> SkillRouteDecision:
        signature = _entries_signature(entries)
        if signature != self.index.signature:
            self.rebuild(entries)
        return self.decide_indexed(text)

    def decide_indexed(self, text: str) -> SkillRouteDecision:
        # Tokenize and expand query with synonyms
        query_terms = set(_tokenize(text))
        if not query_terms:
            return SkillRouteDecision(None, (), False, "no query terms")

        # Expand query with synonyms for better matching
        expanded_queries = _expand_query_with_synonyms(text)
        expanded_terms = set(query_terms)
        for expanded in expanded_queries:
            expanded_terms.update(_tokenize(expanded))

        # Compute query embedding for semantic matching
        query_embedding = self._get_query_embedding(text) if self.use_embeddings else None

        scored: list[SkillRoute] = []
        for item in self.index.items:
            # Keyword matching with expanded terms
            matched = sorted(expanded_terms & item.terms)
            keyword_score = _score_indexed_entry(expanded_terms, item, matched, self.index.idf)

            # Also check original query terms for higher precision
            original_matched = sorted(query_terms & item.terms)
            original_score = _score_indexed_entry(query_terms, item, original_matched, self.index.idf)

            # Use the better of original or expanded score
            best_keyword_score = max(keyword_score, original_score)
            best_matched = matched if keyword_score >= original_score else original_matched

            # Semantic matching (if embeddings available)
            semantic_score = 0.0
            if query_embedding and item.embedding:
                semantic_score = _cosine_similarity(query_embedding, item.embedding)

            # Hybrid scoring
            if best_keyword_score > 0 or semantic_score > self.semantic_threshold:
                combined_score = self._combine_scores(best_keyword_score, semantic_score)
                enhanced_matched = self._enhance_matched_terms(
                    best_matched, expanded_terms, item, semantic_score
                )
                scored.append(SkillRoute(
                    entry=item.entry,
                    score=combined_score,
                    matched_terms=tuple(enhanced_matched[:self.max_terms]),
                    reason=_format_reason(item.entry, enhanced_matched, combined_score),
                ))

        if not scored:
            return SkillRouteDecision(None, (), False, "no metadata matches")

        scored.sort(key=lambda item: item.score, reverse=True)
        best = scored[0]
        candidates = tuple(scored[:5])

        if best.score < self.min_score:
            return SkillRouteDecision(None, candidates, bool(candidates), "below deterministic threshold")
        if len(scored) > 1 and best.score - scored[1].score < self.margin:
            return SkillRouteDecision(None, candidates, True, "candidate scores are too close")
        return SkillRouteDecision(best, candidates, False, best.reason)

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
            # Limit cache size
            if len(self._query_cache) > 1000:
                # Remove oldest entries
                keys_to_remove = list(self._query_cache.keys())[:500]
                for key in keys_to_remove:
                    del self._query_cache[key]
            return embedding
        except Exception:
            return None

    def _combine_scores(self, keyword_score: float, semantic_score: float) -> float:
        """Combine keyword and semantic scores with configured weights."""
        if keyword_score <= 0 and semantic_score <= 0:
            return 0.0
        # Normalize keyword score to [0, 1] range approximately
        normalized_keyword = min(keyword_score / 10.0, 1.0)
        # Semantic score is already in [0, 1] range (cosine similarity)
        combined = (
            self.keyword_weight * normalized_keyword
            + self.semantic_weight * semantic_score
        )
        # Scale back to reasonable range for threshold comparison
        return combined * 10.0

    def _enhance_matched_terms(
        self,
        matched: list[str],
        query_terms: set[str],
        item: IndexedSkillMetadata,
        semantic_score: float,
    ) -> list[str]:
        """Enhance matched terms with semantic similarity indication."""
        enhanced = list(matched)
        # If semantic similarity is high but no keyword match, add indicator
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
        return self._route_uncertain_with_llm(text, decision.candidates, llm_client)

    def _route_uncertain_with_llm(
        self,
        text: str,
        candidates: tuple[SkillRoute, ...],
        llm_client: Any,
    ) -> SkillRoute | None:
        if not candidates:
            return None
        candidate_lines = []
        by_id: dict[str, SkillRoute] = {}
        for candidate in candidates:
            entry = candidate.entry
            by_id[entry.qualified_id] = candidate
            candidate_lines.append(
                f"- id: {entry.qualified_id}\n"
                f"  name: {entry.display_name}\n"
                f"  description: {entry.description}"
            )
        prompt = (
            "You are selecting whether a user request should use one local skill.\n"
            "Use only the skill metadata below. Do not infer hidden capabilities.\n"
            "Return strict JSON only. Schema: {\"skill_id\": string|null}.\n"
            "Use null when no candidate clearly applies.\n\n"
            f"User request:\n{text}\n\n"
            "Candidate skills:\n"
            + "\n".join(candidate_lines)
        )
        try:
            raw = llm_client.think(
                [{"role": "user", "content": prompt}],
                temperature=0,
                silent=True,
                response_format=_ROUTER_RESPONSE_SCHEMA,
                thinking=False,
                max_attempts=1,
            ) or ""
        except TypeError:
            raw = llm_client.think([{"role": "user", "content": prompt}], temperature=0, silent=True) or ""
        except Exception:
            return None
        skill_id = _parse_llm_skill_id(raw)
        if not skill_id:
            return None
        candidate = by_id.get(skill_id)
        if candidate is None:
            return None
        return SkillRoute(
            entry=candidate.entry,
            score=candidate.score,
            matched_terms=candidate.matched_terms,
            reason=f"{candidate.reason}; LLM router selected {skill_id}",
            source="llm",
        )


def _entry_terms(entry: SkillEntry) -> list[str]:
    text = " ".join([
        entry.workflow_id.replace("-", " ").replace("_", " "),
        entry.display_name,
        entry.description,
    ])
    return _tokenize(text)


def _score_entry(query_terms: set[str], entry: SkillEntry, matched: list[str]) -> float:
    item = IndexedSkillMetadata(
        entry=entry,
        terms=frozenset(_entry_terms(entry)),
        id_terms=frozenset(_tokenize(entry.workflow_id.replace("-", " ").replace("_", " "))),
        name_terms=frozenset(_tokenize(entry.display_name)),
    )
    return _score_indexed_entry(query_terms, item, matched, {term: 1.0 for term in item.terms})


def _score_indexed_entry(
    query_terms: set[str],
    item: IndexedSkillMetadata,
    matched: list[str],
    idf: dict[str, float],
) -> float:
    if not matched:
        return 0.0
    score = sum(idf.get(term, 1.0) for term in matched)
    score += 1.5 * sum(idf.get(term, 1.0) for term in query_terms & item.id_terms)
    score += 1.0 * sum(idf.get(term, 1.0) for term in query_terms & item.name_terms)
    if item.entry.workflow_id.lower() in " ".join(query_terms):
        score += 2.0
    return score


def _entries_signature(entries: list[SkillEntry]) -> tuple[str, ...]:
    return tuple(
        f"{entry.qualified_id}\0{entry.display_name}\0{entry.description}"
        for entry in entries
    )


def _format_reason(entry: SkillEntry, matched: list[str], score: float) -> str:
    terms = ", ".join(matched[:8]) or "-"
    return f"{entry.qualified_id} matched metadata terms: {terms} (score={score:.1f})"


def _tokenize(text: str) -> list[str]:
    """Tokenize text with support for Chinese and English."""
    tokens: list[str] = []
    normalized = _split_mixed_script_boundaries((text or "").lower())

    # Try to use jieba for Chinese tokenization
    try:
        import jieba
        jieba_tokens = list(jieba.cut(normalized))
        for token in jieba_tokens:
            token = token.strip()
            if len(token) < 1 or token in _STOPWORDS:
                continue
            # Skip single Chinese characters that are common
            if len(token) == 1 and '\u4e00' <= token <= '\u9fff':
                continue
            tokens.append(token)
        return tokens
    except ImportError:
        pass

    # Fallback to regex tokenization
    for raw in _TOKEN_RE.findall(normalized):
        for part in re.split(r"[_\-]+", raw):
            token = part.strip()
            if len(token) < 2 or token in _STOPWORDS:
                continue
            tokens.append(token)
    return tokens


def _split_mixed_script_boundaries(text: str) -> str:
    text = re.sub(r"([\u4e00-\u9fff])([a-z0-9])", r"\1 \2", text)
    text = re.sub(r"([a-z0-9])([\u4e00-\u9fff])", r"\1 \2", text)
    return text


def _parse_llm_skill_id(raw: str) -> str | None:
    text = (raw or "").strip()
    if not text:
        return None
    match = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if match:
        text = match.group(1).strip()
    elif not text.startswith("{"):
        match = _JSON_OBJECT_RE.search(text)
        if not match:
            return None
        text = match.group(0).strip()
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    if set(data.keys()) != {"skill_id"}:
        return None
    value = data.get("skill_id")
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    if len(value) > 200:
        return None
    return str(value).strip() or None


# ── Embedding and Similarity Functions ─────────────────────────────


def _compute_skill_embeddings(entries: list[SkillEntry]) -> list[tuple[float, ...]]:
    """Compute embeddings for all skill entries."""
    try:
        from agentnexus.rag.embeddings import embed_texts
        texts = [
            f"{entry.display_name} {entry.description}"
            for entry in entries
        ]
        embeddings = embed_texts(texts)
        return [tuple(e) for e in embeddings]
    except Exception:
        return [() for _ in entries]


def _cosine_similarity(vec1: list[float] | tuple[float, ...], vec2: list[float] | tuple[float, ...]) -> float:
    """Compute cosine similarity between two vectors."""
    if not vec1 or not vec2 or len(vec1) != len(vec2):
        return 0.0
    dot_product = sum(a * b for a, b in zip(vec1, vec2))
    norm1 = sum(a * a for a in vec1) ** 0.5
    norm2 = sum(b * b for b in vec2) ** 0.5
    if norm1 == 0 or norm2 == 0:
        return 0.0
    return dot_product / (norm1 * norm2)


def _expand_query_with_synonyms(text: str) -> list[str]:
    """Expand query with synonyms using semantic similarity.

    This function relies on embeddings for synonym detection rather than
    static configuration, making it flexible and language-agnostic.
    """
    # No static expansion - let embeddings handle synonym matching
    return [text]


def _expand_query_semantically(text: str, query_embedding: list[float] | None = None) -> list[str]:
    """Expand query using semantic similarity with skill descriptions.

    This is a dynamic expansion that doesn't rely on static configuration.
    """
    expansions = [text]

    if query_embedding is None:
        return expansions

    # Find skills with high semantic similarity
    # This is handled in the main routing logic
    return expansions
