"""LLM-based skill decision with conversation context and user preferences.

The LLM receives:
- User's current message
- Top-K candidate skills (from the recommender)
- Conversation history summary
- LTM user preferences (e.g., "don't use skill X")

And decides: use_skill / skip_skill / clarify.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from agentnexus.skills.router.types import _JSON_OBJECT_RE, SkillRoute


@dataclass(frozen=True)
class LLMDecision:
    """Result of LLM routing decision."""
    action: str  # "use_skill" | "skip_skill" | "clarify"
    skill_id: str | None = None
    reason: str = ""
    clarify_question: str | None = None


_DECIDER_RESPONSE_SCHEMA = {"type": "json_object"}


def decide_with_llm(
    text: str,
    candidates: list[SkillRoute],
    llm_client: Any,
    *,
    conversation_context: str | None = None,
    user_preferences: str | None = None,
) -> LLMDecision:
    """Ask the LLM to decide whether to activate a skill.

    The LLM has access to conversation history and user preferences,
    so it can respect instructions like "don't use skill X".
    """
    if not candidates:
        return LLMDecision(action="skip_skill", reason="no candidates")

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

    context_block = ""
    if conversation_context:
        context_block = f"\nConversation context:\n{conversation_context}\n"

    prefs_block = ""
    if user_preferences:
        prefs_block = f"\nUser preferences (MUST respect):\n{user_preferences}\n"

    prompt = (
        "You are deciding whether a user request should trigger a local skill.\n"
        "Consider the user's message, available skills, conversation context,\n"
        "and user preferences. User preferences MUST be respected.\n\n"
        "Return strict JSON only.\n"
        'Schema: {"action": "use_skill"|"skip_skill"|"clarify", '
        '"skill_id": string|null, "reason": string, '
        '"clarify_question": string|null}\n\n'
        "- use_skill: activate the specified skill_id\n"
        "- skip_skill: handle as general conversation, no skill needed\n"
        "- clarify: ask the user which skill they want (provide clarify_question)\n\n"
        f"User request:\n{text}\n"
        f"{context_block}{prefs_block}\n"
        "Candidate skills (ranked by relevance):\n"
        + "\n".join(candidate_lines)
    )

    try:
        raw = llm_client.think(
            [{"role": "user", "content": prompt}],
            temperature=0,
            silent=True,
            response_format=_DECIDER_RESPONSE_SCHEMA,
            thinking=False,
            max_attempts=1,
        ) or ""
    except TypeError:
        raw = llm_client.think(
            [{"role": "user", "content": prompt}],
            temperature=0,
            silent=True,
        ) or ""
    except Exception:
        return LLMDecision(action="skip_skill", reason="LLM call failed")

    return _parse_decision(raw, by_id)


def _parse_decision(raw: str, by_id: dict[str, SkillRoute]) -> LLMDecision:
    """Parse LLM JSON response into an LLMDecision."""
    text = (raw or "").strip()
    if not text:
        return LLMDecision(action="skip_skill", reason="empty LLM response")

    # Extract JSON from markdown code blocks or raw text
    match = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if match:
        text = match.group(1).strip()
    elif not text.startswith("{"):
        match = _JSON_OBJECT_RE.search(text)
        if not match:
            return LLMDecision(action="skip_skill", reason="no JSON in LLM response")
        text = match.group(0).strip()

    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return LLMDecision(action="skip_skill", reason="invalid JSON from LLM")

    if not isinstance(data, dict):
        return LLMDecision(action="skip_skill", reason="LLM returned non-dict")

    action = data.get("action", "skip_skill")
    if action not in ("use_skill", "skip_skill", "clarify"):
        action = "skip_skill"

    skill_id = data.get("skill_id")
    if isinstance(skill_id, str):
        skill_id = skill_id.strip() or None
    else:
        skill_id = None

    reason = data.get("reason", "")
    if not isinstance(reason, str):
        reason = str(reason)

    clarify_question = data.get("clarify_question")
    if isinstance(clarify_str := clarify_question, str):
        clarify_question = clarify_str.strip() or None
    else:
        clarify_question = None

    # Validate skill_id exists in candidates
    if action == "use_skill" and skill_id:
        if skill_id not in by_id:
            return LLMDecision(
                action="skip_skill",
                reason=f"LLM selected unknown skill {skill_id}",
            )
    elif action == "use_skill" and not skill_id:
        action = "skip_skill"
        reason = "LLM chose use_skill but no skill_id"

    return LLMDecision(
        action=action,
        skill_id=skill_id,
        reason=reason,
        clarify_question=clarify_question,
    )
