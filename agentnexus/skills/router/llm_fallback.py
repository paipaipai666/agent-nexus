"""LLM-based disambiguation for uncertain routing cases."""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)
import re
from typing import Any

from agentnexus.skills.router.normalize import tokenize
from agentnexus.skills.router.parse import extract_intent_signals
from agentnexus.skills.router.types import (
    _JSON_OBJECT_RE,
    _ROUTER_RESPONSE_SCHEMA,
    SkillRoute,
)


def route_with_llm(
    text: str,
    candidates: tuple[SkillRoute, ...],
    reason: str,
    llm_client: Any,
) -> SkillRoute | None:
    """Use LLM to disambiguate between close candidates."""
    if not candidates:
        return None

    query_terms = set(tokenize(text))
    intent = extract_intent_signals(text, query_terms)

    by_id: dict[str, SkillRoute] = {}
    candidate_lines: list[str] = []
    for rank, candidate in enumerate(candidates, 1):
        entry = candidate.entry
        by_id[entry.qualified_id] = candidate
        candidate_lines.append(
            f"{rank}. id: {entry.qualified_id}\n"
            f"   name: {entry.display_name}\n"
            f"   description: {entry.description}\n"
            f"   matched terms: {', '.join(candidate.matched_terms) or '-'}\n"
            f"   score: {candidate.score:.2f}"
        )

    intent_summary = ""
    if intent.primary_action or intent.primary_object:
        intent_summary = (
            f"\nDetected intent: action={intent.primary_action or '-'}, "
            f"object={intent.primary_object or '-'}, "
            f"mode={intent.priority_mode}"
        )

    prompt = (
        "You are selecting whether a user request should trigger one local skill.\n"
        "Use ONLY the skill metadata below. Do not infer hidden capabilities.\n"
        "Return strict JSON only.\n"
        'Schema: {"skill_id": string|null, "confidence": number, "reason": string}\n'
        "Use null when no candidate clearly applies.\n"
        "confidence: 0.0-1.0, your certainty in the selection.\n\n"
        f"User request:\n{text}\n\n"
        f"Uncertainty reason: {reason}{intent_summary}\n\n"
        "Candidate skills (ranked by score):\n"
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
        raw = llm_client.think(
            [{"role": "user", "content": prompt}],
            temperature=0,
            silent=True,
        ) or ""
    except Exception as exc:
        logger.warning("LLM routing fallback failed: %s", exc)
        return None

    skill_id = parse_llm_skill_id(raw)
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


def parse_llm_skill_id(raw: str) -> str | None:
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
    value = data.get("skill_id")
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    if len(value) > 200:
        return None
    return str(value).strip() or None
