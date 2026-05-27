"""Router v2 — modular skill recommendation and LLM-based decision.

Submodules:
    types        — dataclasses, constants, lexicons
    normalize    — tokenization, fuzzy matching, text normalization
    parse        — query understanding and intent extraction
    retrieve     — index building and candidate retrieval
    rank         — scoring, metadata alignment, reranking
    decide       — SkillRecommender (ranks skills, does NOT decide)
    llm_decider  — LLM-based decision with context and preferences
    llm_fallback — legacy LLM disambiguation (backward compat)
    telemetry    — route event logging
"""

from agentnexus.skills.registry import SkillEntry
from agentnexus.skills.router.decide import SkillRecommender, SkillRouter, format_reason
from agentnexus.skills.router.llm_decider import LLMDecision, decide_with_llm
from agentnexus.skills.router.llm_fallback import parse_llm_skill_id, route_with_llm
from agentnexus.skills.router.normalize import (
    augment_tokens_with_known_phrases,
    fuzzy_match_term,
    levenshtein_distance,
    split_mixed_script_boundaries,
    tokenize,
)
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
from agentnexus.skills.router.retrieve import (
    build_index,
    compute_skill_embeddings,
    entries_signature,
    entry_terms,
    infer_aliases,
    infer_objects,
    infer_verbs,
)
from agentnexus.skills.router.telemetry import RouteEvent, RouteTelemetry
from agentnexus.skills.router.types import (
    IndexedSkillMetadata,
    IntentSignals,
    SkillRoute,
    SkillRouteDecision,
    SkillRouterIndex,
)

# Backward-compatible aliases
_score_indexed_entry = score_indexed_entry
_score_metadata_alignment = score_metadata_alignment
_score_fuzzy_match = score_fuzzy_match
_extract_intent_signals = extract_intent_signals
_rerank_with_intent = rerank_with_intent
_best_candidate_is_intent_confident = best_candidate_is_intent_confident
_parse_llm_skill_id = parse_llm_skill_id
_tokenize = tokenize
_entry_terms = entry_terms
_entries_signature = entries_signature
_infer_verbs = infer_verbs
_infer_objects = infer_objects
_infer_aliases = infer_aliases
_format_reason = format_reason
_cosine_similarity = cosine_similarity
_compute_skill_embeddings = compute_skill_embeddings
_levenshtein_distance = levenshtein_distance
_fuzzy_match_term = fuzzy_match_term
_split_mixed_script_boundaries = split_mixed_script_boundaries
_augment_tokens_with_known_phrases = augment_tokens_with_known_phrases


def _score_entry(
    query_terms: set[str], entry: SkillEntry, matched: list[str],
) -> float:
    """Backward-compatible wrapper that scores a single entry without an index."""
    item = IndexedSkillMetadata(
        entry=entry,
        terms=frozenset(entry_terms(entry)),
        id_terms=frozenset(
            tokenize(entry.workflow_id.replace("-", " ").replace("_", " ")),
        ),
        name_terms=frozenset(tokenize(entry.display_name)),
    )
    return score_indexed_entry(
        query_terms, item, matched, {term: 1.0 for term in item.terms},
    )


__all__ = [
    # Types
    "SkillRoute",
    "SkillRouteDecision",
    "IntentSignals",
    "IndexedSkillMetadata",
    "SkillRouterIndex",
    "LLMDecision",
    # Main classes
    "SkillRecommender",
    "SkillRouter",  # backward-compat alias
    # Functions
    "decide_with_llm",
    "route_with_llm",  # backward-compat
    "tokenize",
    "extract_intent_signals",
    "score_indexed_entry",
    "score_metadata_alignment",
    "score_fuzzy_match",
    "score_example_similarity",
    "rerank_with_intent",
    "best_candidate_is_intent_confident",
    "cosine_similarity",
    "build_index",
    "compute_skill_embeddings",
    "entry_terms",
    "entries_signature",
    "infer_verbs",
    "infer_objects",
    "infer_aliases",
    "format_reason",
    "parse_llm_skill_id",
    "levenshtein_distance",
    "fuzzy_match_term",
    "split_mixed_script_boundaries",
    "augment_tokens_with_known_phrases",
    # Telemetry
    "RouteEvent",
    "RouteTelemetry",
]
