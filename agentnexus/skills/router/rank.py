"""Scoring, metadata alignment, fuzzy matching, and reranking.

All scoring is driven by structured skill metadata (verbs, objects, aliases,
domains, examples, negative_hints). No case-by-case hardcoded rules.
"""

from __future__ import annotations

from agentnexus.skills.router.normalize import fuzzy_match_term, tokenize
from agentnexus.skills.router.types import (
    _PRODUCT_ALIASES,
    _VERB_FORMS,
    IndexedSkillMetadata,
    IntentSignals,
    SkillRoute,
    SkillRouterIndex,
)


def score_indexed_entry(
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


def score_metadata_alignment(
    query_terms: set[str],
    item: IndexedSkillMetadata,
    idf: dict[str, float],
    *,
    fuzzy: bool = False,
    intent: IntentSignals | None = None,
) -> float:
    """Score alignment between query terms and structured metadata.

    All signals come from the skill's own metadata fields — no hardcoded
    per-skill or per-keyword rules.
    """
    bonus = 0.0
    q_lower = {t.lower() for t in query_terms}

    # Verb alignment (high signal — verbs are the strongest intent indicator)
    verb_hits = q_lower & item.verb_terms
    if verb_hits:
        bonus += 2.0 * sum(idf.get(t, 1.0) for t in verb_hits)

    # Object alignment
    obj_hits = q_lower & item.object_terms
    if obj_hits:
        bonus += 2.0 * sum(idf.get(t, 1.0) for t in obj_hits)

    # Verb+Object combo bonus: when both verb and object match the same skill,
    # that skill is a much stronger candidate than one matching only the object.
    if verb_hits and obj_hits:
        combo_bonus = min(len(verb_hits), len(obj_hits)) * 2.0
        bonus += combo_bonus

    # Alias alignment
    alias_hits = q_lower & item.alias_terms
    if alias_hits:
        bonus += 1.5 * sum(idf.get(t, 1.0) for t in alias_hits)

    # Product name aliases (word→docx, etc.)
    for term in q_lower:
        if term in _PRODUCT_ALIASES:
            product_tokens = _PRODUCT_ALIASES[term]
            for pt in product_tokens:
                if pt.lower() in item.terms or pt.lower() in item.alias_terms:
                    bonus += 1.5 * idf.get(pt.lower(), 1.0)

    # Intent-aware alignment (generic — uses skill metadata, not hardcoded rules)
    if intent is not None:
        bonus += _score_intent_alignment(q_lower, item, idf, intent)

    # Domain alignment
    domain_hits = q_lower & item.domain_terms
    if domain_hits:
        bonus += 1.5 * sum(idf.get(t, 1.0) for t in domain_hits)

    # Example-based matching
    if item.example_terms:
        example_hits = q_lower & item.example_terms
        if example_hits:
            bonus += 2.5 * sum(idf.get(t, 1.0) for t in example_hits)

    # Negative hint penalty
    if item.negative_hint_terms:
        neg_hits = q_lower & item.negative_hint_terms
        if neg_hits:
            bonus -= 3.0 * len(neg_hits)

    if fuzzy:
        bonus *= 0.7
    return bonus


def _score_intent_alignment(
    q_lower: set[str],
    item: IndexedSkillMetadata,
    idf: dict[str, float],
    intent: IntentSignals,
) -> float:
    """Score intent alignment using structured skill metadata only.

    No hardcoded per-keyword or per-skill rules.
    """
    bonus = 0.0

    # Primary action matches skill's declared verbs
    if intent.primary_action and intent.primary_action.lower() in item.verb_terms:
        bonus += 2.0

    # Primary object matches skill's declared objects/aliases/terms
    if intent.primary_object:
        object_lower = intent.primary_object.lower()
        if object_lower in item.object_terms:
            bonus += 2.0
        if object_lower in item.alias_terms or object_lower in item.terms:
            bonus += 1.5

    # Verb+Object combo: when both primary action and object match this skill,
    # it's a strong signal that this skill handles the full intent.
    if (
        intent.primary_action
        and intent.primary_object
        and intent.primary_action.lower() in item.verb_terms
        and (
            intent.primary_object.lower() in item.object_terms
            or intent.primary_object.lower() in item.alias_terms
        )
    ):
        bonus += 3.0

    # Multiple verbs matching (composite intent)
    action_set = {action for action, _ in intent.action_verbs}
    verb_overlap = {a.lower() for a in action_set} & item.verb_terms
    if len(verb_overlap) > 1:
        bonus += 2.0 * (len(verb_overlap) - 1)

    return bonus


def score_fuzzy_match(
    query_terms: set[str],
    item: IndexedSkillMetadata,
    idf: dict[str, float],
) -> tuple[list[str], float]:
    """Fuzzy match query terms against skill canonical tokens.

    Returns (matched_terms, score). Only called when exact match is empty.
    """
    fuzzy_matched: list[str] = []
    fuzzy_score = 0.0
    canonical_set = set(item.canonical_tokens)

    for term in query_terms:
        if term in canonical_set:
            continue
        best_term, best_ratio = fuzzy_match_term(term, item.canonical_tokens)
        if best_term:
            fuzzy_matched.append(f"fuzzy({term}→{best_term})")
            multiplier = 0.7
            if best_term in item.alias_terms:
                multiplier += 0.25
            if best_term in item.object_terms:
                multiplier += 0.2
            if best_term in item.verb_terms:
                multiplier += 0.2
            fuzzy_score += idf.get(best_term, 1.0) * best_ratio * multiplier

    # Also try product aliases
    for term in query_terms:
        if term in _PRODUCT_ALIASES:
            for alias in _PRODUCT_ALIASES[term]:
                if alias.lower() in canonical_set:
                    marker = f"fuzzy({term}→{alias.lower()})"
                    if marker not in fuzzy_matched:
                        fuzzy_matched.append(marker)
                        fuzzy_score += idf.get(alias.lower(), 1.0) * 0.75
                        break

    # Fuzzy-match against known verb lexicon
    for term in query_terms:
        if term in canonical_set:
            continue
        best_term, best_ratio = fuzzy_match_term(term, _VERB_FORMS)
        if best_term and best_term in item.verb_terms:
            marker = f"fuzzy({term}→{best_term})"
            if marker not in fuzzy_matched:
                fuzzy_matched.append(marker)
                fuzzy_score += idf.get(best_term, 1.0) * max(best_ratio, 0.8)

    return fuzzy_matched, fuzzy_score


def rerank_with_intent(
    scored: list[SkillRoute],
    intent: IntentSignals,
    index: SkillRouterIndex,
) -> list[SkillRoute]:
    """Rerank candidates using generic intent-metadata alignment.

    No hardcoded per-skill or per-keyword rules — all signals come from
    the skill's declared verbs, objects, and aliases.
    """
    if not intent.primary_action and not intent.primary_object:
        return scored

    metadata_map = {item.entry.qualified_id: item for item in index.items}
    reranked: list[tuple[float, SkillRoute]] = []

    for route in scored:
        adjustment = 0.0
        item = metadata_map.get(route.entry.qualified_id)
        if item is not None:
            # Primary action alignment
            if intent.primary_action:
                action_lower = intent.primary_action.lower()
                if action_lower in item.verb_terms:
                    adjustment += 2.0

            # Primary object alignment
            if intent.primary_object:
                object_lower = intent.primary_object.lower()
                object_weight = 1.0 if intent.priority_mode == "parallel" else 3.0
                alias_weight = 0.5 if intent.priority_mode == "parallel" else 2.0
                if object_lower in item.object_terms:
                    adjustment += object_weight
                if object_lower in item.alias_terms or object_lower in item.terms:
                    adjustment += alias_weight

            # Verb+Object combo: strong signal when both match
            if (
                intent.primary_action
                and intent.primary_object
                and intent.primary_action.lower() in item.verb_terms
                and (
                    intent.primary_object.lower() in item.object_terms
                    or intent.primary_object.lower() in item.alias_terms
                )
            ):
                adjustment += 4.0

            # Alias hits from matched terms
            query_in_route = set()
            for matched_term in route.matched_terms:
                clean = matched_term.replace("fuzzy(", "").split("→")[0]
                query_in_route.add(clean.lower())
            alias_hits = query_in_route & item.alias_terms
            if alias_hits:
                adjustment += 1.0 * len(alias_hits)

        reranked.append((route.score + adjustment, route))

    reranked.sort(key=lambda x: x[0], reverse=True)
    return [
        SkillRoute(
            entry=r.entry,
            score=total,
            matched_terms=r.matched_terms,
            reason=r.reason,
            source=r.source,
        )
        for total, r in reranked
    ]


def best_candidate_is_intent_confident(
    best: SkillRoute,
    runner_up: SkillRoute,
    intent: IntentSignals,
    index: SkillRouterIndex,
) -> bool:
    """Allow close scores when top candidate strongly matches primary intent.

    Generic comparison — no hardcoded per-keyword rules.
    """
    metadata_map = {item.entry.qualified_id: item for item in index.items}
    best_item = metadata_map.get(best.entry.qualified_id)
    runner_item = metadata_map.get(runner_up.entry.qualified_id)
    if best_item is None:
        return False

    best_hits = 0
    runner_hits = 0

    # Primary object alignment
    if intent.primary_object:
        object_lower = intent.primary_object.lower()
        if (
            object_lower in best_item.object_terms
            or object_lower in best_item.alias_terms
            or object_lower in best_item.terms
        ):
            best_hits += 2
        if runner_item is not None and (
            object_lower in runner_item.object_terms
            or object_lower in runner_item.alias_terms
            or object_lower in runner_item.terms
        ):
            runner_hits += 2

    # Primary action alignment
    if intent.primary_action:
        action_lower = intent.primary_action.lower()
        if action_lower in best_item.verb_terms:
            best_hits += 2
        if runner_item is not None and action_lower in runner_item.verb_terms:
            runner_hits += 2

    # Verb+Object combo on best
    if (
        intent.primary_action
        and intent.primary_object
        and intent.primary_action.lower() in best_item.verb_terms
        and (
            intent.primary_object.lower() in best_item.object_terms
            or intent.primary_object.lower() in best_item.alias_terms
        )
    ):
        best_hits += 2

    matched_text = " ".join(best.matched_terms).lower()
    if intent.primary_object and intent.primary_object.lower() in matched_text:
        best_hits += 1
    if intent.primary_action and intent.primary_action.lower() in matched_text:
        best_hits += 1

    return best_hits >= 2 and best_hits > runner_hits


def score_example_similarity(
    query_terms: set[str],
    item: IndexedSkillMetadata,
    idf: dict[str, float],
) -> tuple[list[str], float]:
    """Score query against each skill example text directly.

    Returns (matched_examples, score). Compares query terms against
    per-example token sets, not the aggregated example_terms.
    """
    if not item.example_texts:
        return [], 0.0

    matched_examples: list[str] = []
    best_score = 0.0

    for example in item.example_texts:
        example_tokens = set(tokenize(example))
        if not example_tokens:
            continue
        overlap = query_terms & example_tokens
        if not overlap:
            continue
        score = sum(idf.get(t, 1.0) for t in overlap)
        # Bonus for high overlap ratio (query closely matches an example)
        overlap_ratio = len(overlap) / max(len(query_terms), 1)
        score *= 1.0 + overlap_ratio
        if score > best_score:
            best_score = score
            matched_examples = [f"example_match({example[:30]})"]

    return matched_examples, best_score


def cosine_similarity(
    vec1: list[float] | tuple[float, ...],
    vec2: list[float] | tuple[float, ...],
) -> float:
    if not vec1 or not vec2 or len(vec1) != len(vec2):
        return 0.0
    dot_product = sum(a * b for a, b in zip(vec1, vec2))
    norm1 = sum(a * a for a in vec1) ** 0.5
    norm2 = sum(b * b for b in vec2) ** 0.5
    if norm1 == 0 or norm2 == 0:
        return 0.0
    return dot_product / (norm1 * norm2)
