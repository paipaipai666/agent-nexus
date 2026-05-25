"""Deterministic metadata router for SKILL.md driven skills."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
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


@dataclass(frozen=True)
class SkillRouterIndex:
    items: tuple[IndexedSkillMetadata, ...]
    idf: dict[str, float]
    signature: tuple[str, ...]

    @classmethod
    def build(cls, entries: list[SkillEntry]) -> "SkillRouterIndex":
        items: list[IndexedSkillMetadata] = []
        doc_freq: dict[str, int] = {}
        for entry in entries:
            id_terms = frozenset(_tokenize(entry.workflow_id.replace("-", " ").replace("_", " ")))
            name_terms = frozenset(_tokenize(entry.display_name))
            terms = frozenset(_entry_terms(entry))
            items.append(IndexedSkillMetadata(
                entry=entry,
                terms=terms,
                id_terms=id_terms,
                name_terms=name_terms,
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
    """Route a user request to a skill using only always-visible metadata."""

    def __init__(
        self,
        *,
        min_score: float = 2.0,
        margin: float = 0.75,
        max_terms: int = 8,
    ):
        self.min_score = min_score
        self.margin = margin
        self.max_terms = max_terms
        self.index = SkillRouterIndex.build([])

    def rebuild(self, entries: list[SkillEntry]) -> None:
        self.index = SkillRouterIndex.build(entries)

    def route(self, text: str, entries: list[SkillEntry]) -> SkillRoute | None:
        return self.decide(text, entries).route

    def decide(self, text: str, entries: list[SkillEntry]) -> SkillRouteDecision:
        signature = _entries_signature(entries)
        if signature != self.index.signature:
            self.rebuild(entries)
        return self.decide_indexed(text)

    def decide_indexed(self, text: str) -> SkillRouteDecision:
        query_terms = set(_tokenize(text))
        if not query_terms:
            return SkillRouteDecision(None, (), False, "no query terms")
        scored: list[SkillRoute] = []
        for item in self.index.items:
            matched = sorted(query_terms & item.terms)
            score = _score_indexed_entry(query_terms, item, matched, self.index.idf)
            if score <= 0:
                continue
            scored.append(SkillRoute(
                entry=item.entry,
                score=score,
                matched_terms=tuple(matched[:self.max_terms]),
                reason=_format_reason(item.entry, matched, score),
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
    tokens: list[str] = []
    normalized = _split_mixed_script_boundaries((text or "").lower())
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
