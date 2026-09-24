"""Index building and candidate retrieval for skill routing."""

from __future__ import annotations

import logging
import math

logger = logging.getLogger(__name__)

from agentnexus.skills.registry import SkillEntry
from agentnexus.skills.router.normalize import tokenize
from agentnexus.skills.router.types import (
    _ABBREVIATION_MAP,
    IndexedSkillMetadata,
    SkillRouterIndex,
)


def entry_body_text(entry: SkillEntry, max_chars: int = 2000) -> str:
    """Concatenate skill body signals for routing (steps, criteria, resource names).

    SKILL.md body lands in workflow steps as guidance prompts; including it
    lets the router distinguish same-topic skills that share a short description.
    """
    parts: list[str] = []
    for step in getattr(entry.workflow, "steps", None) or []:
        if getattr(step, "prompt", None):
            parts.append(step.prompt)
        if getattr(step, "tool", None):
            parts.append(step.tool)
    for item in getattr(entry.workflow, "success_criteria", None) or []:
        parts.append(str(item))
    for res in getattr(entry.workflow, "resources", None) or []:
        name = getattr(res, "name", "") or getattr(res, "path", "")
        if name:
            parts.append(str(name))
    text = "\n".join(parts)
    return text[:max_chars]


def entry_terms(entry: SkillEntry) -> list[str]:
    text = " ".join([
        entry.workflow_id.replace("-", " ").replace("_", " "),
        entry.display_name,
        entry.description,
    ])
    return tokenize(text)


def entry_body_terms(entry: SkillEntry) -> list[str]:
    body = entry_body_text(entry)
    return tokenize(body) if body else []


def synthesize_intent_queries(entry: SkillEntry, max_queries: int = 8) -> list[str]:
    """Build lightweight user-intent phrasings from skill metadata (Toollery-style).

    Offline, deterministic — no LLM. Expands verbs×objects and example lines so
    retrieval can match how users phrase requests rather than skill titles only.
    """
    intents: list[str] = []
    seen: set[str] = set()

    def _add(text: str) -> None:
        cleaned = " ".join((text or "").split())
        if cleaned and cleaned.lower() not in seen:
            seen.add(cleaned.lower())
            intents.append(cleaned)

    for example in entry.examples or ():
        _add(example)

    verbs = list(entry.verbs) or infer_verbs(entry)
    objects = list(entry.objects) or infer_objects(entry)
    name = entry.display_name or entry.workflow_id.replace("-", " ").replace("_", " ")
    for verb in verbs[:3]:
        for obj in objects[:3]:
            _add(f"{verb}{obj}")
            _add(f"帮我{verb}{obj}")
        _add(f"{verb}{name}")
    for obj in objects[:3]:
        _add(f"{obj}相关的{verbs[0] if verbs else '处理'}")
    # Headings from body (## Step …) act as scenario cues
    for line in entry_body_text(entry, 800).splitlines():
        stripped = line.strip().lstrip("#").strip()
        if 4 <= len(stripped) <= 40 and not stripped.endswith(("。", ".", ":")):
            _add(stripped)
        if len(intents) >= max_queries:
            break
    return intents[:max_queries]


def entry_intent_terms(entry: SkillEntry) -> list[str]:
    terms: list[str] = []
    for phrase in synthesize_intent_queries(entry):
        terms.extend(tokenize(phrase))
    return terms


def entries_signature(entries: list[SkillEntry]) -> tuple[str, ...]:
    return tuple(
        f"{entry.qualified_id}\0{entry.display_name}\0{entry.description}"
        f"\0{'|'.join(entry.domains)}\0{'|'.join(entry.examples)}\0{'|'.join(entry.negative_hints)}"
        f"\0{entry_body_text(entry, 400)}"
        for entry in entries
    )


def infer_verbs(entry: SkillEntry) -> list[str]:
    """Infer action verbs from entry metadata if not explicitly provided."""
    from agentnexus.skills.router.types import _VERB_LEXICON

    if entry.verbs:
        return list(entry.verbs)
    text = f"{entry.display_name} {entry.description}".lower()
    found: list[str] = []
    for canonical, synonyms in _VERB_LEXICON.items():
        all_forms = [canonical] + synonyms
        if any(form in text for form in all_forms):
            found.append(canonical)
            found.extend(synonyms)
    return found


def infer_objects(entry: SkillEntry) -> list[str]:
    """Infer object nouns from entry metadata if not explicitly provided."""
    from agentnexus.skills.router.types import _OBJECT_LEXICON

    if entry.objects:
        return list(entry.objects)
    text = f"{entry.display_name} {entry.description}".lower()
    found: list[str] = []
    for canonical, synonyms in _OBJECT_LEXICON.items():
        all_forms = [canonical] + synonyms
        if any(form in text for form in all_forms):
            found.append(canonical)
            found.extend(synonyms)
    return found


def infer_aliases(entry: SkillEntry) -> list[str]:
    """Infer aliases from entry metadata if not explicitly provided."""
    if entry.aliases:
        return list(entry.aliases)
    aliases: list[str] = []
    wid = entry.workflow_id.lower()
    for abbrev, expanded in _ABBREVIATION_MAP.items():
        if wid.startswith(abbrev) or abbrev in wid:
            aliases.append(abbrev)
            aliases.append(expanded)
    aliases.append(wid)
    aliases.append(entry.display_name.lower())
    return aliases


def build_index(
    entries: list[SkillEntry],
    *,
    compute_embeddings: bool = True,
    embeddings: list[tuple[float, ...]] | None = None,
) -> SkillRouterIndex:
    """Build a SkillRouterIndex from skill entries.

    Pass precomputed ``embeddings`` to reuse vectors computed elsewhere
    (e.g. a background warm-up thread) instead of recomputing inline.
    """
    items: list[IndexedSkillMetadata] = []
    doc_freq: dict[str, int] = {}

    if embeddings is None:
        embeddings = compute_skill_embeddings(entries) if compute_embeddings and entries else []
    else:
        embeddings = list(embeddings)

    for i, entry in enumerate(entries):
        id_terms = frozenset(tokenize(
            entry.workflow_id.replace("-", " ").replace("_", " "),
        ))
        name_terms = frozenset(tokenize(entry.display_name))
        terms = frozenset(entry_terms(entry))
        body_terms = frozenset(entry_body_terms(entry))
        intent_texts = tuple(synthesize_intent_queries(entry))
        intent_terms = frozenset(entry_intent_terms(entry))
        embedding = embeddings[i] if i < len(embeddings) else ()

        verb_terms = frozenset(infer_verbs(entry))
        object_terms = frozenset(infer_objects(entry))
        alias_terms = frozenset(infer_aliases(entry))
        domain_terms = frozenset(
            t for d in entry.domains for t in tokenize(d)
        )
        example_texts = tuple(entry.examples)
        example_terms = frozenset(
            t for ex in entry.examples for t in tokenize(ex)
        )
        negative_hint_terms = frozenset(
            t for hint in entry.negative_hints for t in tokenize(hint)
        )
        canonical = tuple(sorted(terms | body_terms | intent_terms | alias_terms | example_terms))

        items.append(IndexedSkillMetadata(
            entry=entry,
            terms=terms,
            id_terms=id_terms,
            name_terms=name_terms,
            body_terms=body_terms,
            intent_texts=intent_texts,
            intent_terms=intent_terms,
            verb_terms=verb_terms,
            object_terms=object_terms,
            alias_terms=alias_terms,
            domain_terms=domain_terms,
            example_texts=example_texts,
            example_terms=example_terms,
            negative_hint_terms=negative_hint_terms,
            canonical_tokens=canonical,
            embedding=embedding,
        ))
        for term in terms | body_terms | intent_terms | example_terms:
            doc_freq[term] = doc_freq.get(term, 0) + 1

    count = max(len(items), 1)
    idf = {
        term: 1.0 + math.log((count + 1) / (freq + 1))
        for term, freq in doc_freq.items()
    }
    return SkillRouterIndex(
        items=tuple(items),
        idf=idf,
        signature=entries_signature(entries),
    )


def compute_skill_embeddings(
    entries: list[SkillEntry],
) -> list[tuple[float, ...]]:
    try:
        from agentnexus.rag.embeddings import embed_texts
        texts = [
            f"{entry.display_name} {entry.description}\n{entry_body_text(entry, 800)}"
            for entry in entries
        ]
        embeddings = embed_texts(texts)
        return [tuple(e) for e in embeddings]
    except Exception as exc:
        logger.warning("Skill embedding computation failed, falling back to keyword-only routing: %s", exc)
        return [() for _ in entries]
