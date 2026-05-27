"""Index building and candidate retrieval for skill routing."""

from __future__ import annotations

import math

from agentnexus.skills.registry import SkillEntry
from agentnexus.skills.router.normalize import tokenize
from agentnexus.skills.router.types import (
    _ABBREVIATION_MAP,
    IndexedSkillMetadata,
    SkillRouterIndex,
)


def entry_terms(entry: SkillEntry) -> list[str]:
    text = " ".join([
        entry.workflow_id.replace("-", " ").replace("_", " "),
        entry.display_name,
        entry.description,
    ])
    return tokenize(text)


def entries_signature(entries: list[SkillEntry]) -> tuple[str, ...]:
    return tuple(
        f"{entry.qualified_id}\0{entry.display_name}\0{entry.description}"
        f"\0{'|'.join(entry.domains)}\0{'|'.join(entry.examples)}\0{'|'.join(entry.negative_hints)}"
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
) -> SkillRouterIndex:
    """Build a SkillRouterIndex from skill entries."""
    items: list[IndexedSkillMetadata] = []
    doc_freq: dict[str, int] = {}

    embeddings: list[tuple[float, ...]] = []
    if compute_embeddings and entries:
        embeddings = compute_skill_embeddings(entries)

    for i, entry in enumerate(entries):
        id_terms = frozenset(tokenize(
            entry.workflow_id.replace("-", " ").replace("_", " "),
        ))
        name_terms = frozenset(tokenize(entry.display_name))
        terms = frozenset(entry_terms(entry))
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
        canonical = tuple(sorted(terms | alias_terms | example_terms))

        items.append(IndexedSkillMetadata(
            entry=entry,
            terms=terms,
            id_terms=id_terms,
            name_terms=name_terms,
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
        for term in terms | example_terms:
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
        texts = [f"{entry.display_name} {entry.description}" for entry in entries]
        embeddings = embed_texts(texts)
        return [tuple(e) for e in embeddings]
    except Exception:
        return [() for _ in entries]
