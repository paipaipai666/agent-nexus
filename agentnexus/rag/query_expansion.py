"""LLM-assisted query rewrite, expansion, and HyDE generation."""

from __future__ import annotations

from agentnexus.core.config import get_settings
from agentnexus.core.llm import AgentLLM
from agentnexus.prompts import load_prompt

QUERY_REWRITE_PROMPT = load_prompt("rag_query_rewrite")
MULTI_QUERY_PROMPT = load_prompt("rag_multi_query")
HYDE_PROMPT = load_prompt("rag_hyde")


def dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = value.strip()
        if not normalized:
            continue
        key = normalized.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(normalized)
    return result


def looks_like_question(query: str) -> bool:
    normalized = query.strip()
    if not normalized:
        return False
    question_tokens = (
        "?",
        "？",
        "什么",
        "如何",
        "怎么",
        "怎样",
        "为何",
        "为什么",
        "是否",
        "哪种",
        "哪些",
        "谁",
        "when",
        "what",
        "why",
        "how",
        "which",
    )
    lowered = normalized.casefold()
    return any(token in normalized or token in lowered for token in question_tokens)


def rewrite_query(query: str, llm: AgentLLM | None = None) -> str:
    settings = get_settings()
    if not settings.enable_query_rewrite:
        return query
    try:
        llm_client = llm or AgentLLM()
        prompt = QUERY_REWRITE_PROMPT.format(query=query)
        rewritten = llm_client.think([{"role": "user", "content": prompt}], temperature=0, silent=True)
        rewritten = (rewritten or "").strip()
        if len(rewritten) >= 2:
            return rewritten
    except Exception:
        pass
    return query


def expand_queries(query: str, llm: AgentLLM | None = None) -> list[str]:
    settings = get_settings()
    rewritten = rewrite_query(query, llm=llm)
    queries = [rewritten]
    if not settings.enable_multi_query:
        return queries
    try:
        llm_client = llm or AgentLLM()
        prompt = MULTI_QUERY_PROMPT.format(
            query=query,
            rewritten_query=rewritten,
            count=settings.rag_multi_query_count,
        )
        expanded = llm_client.think([{"role": "user", "content": prompt}], temperature=0, silent=True)
        candidates = []
        for line in (expanded or "").splitlines():
            normalized = line.strip().lstrip("-").lstrip("0123456789.").strip()
            if normalized:
                candidates.append(normalized)
        queries.extend(candidates)
    except Exception:
        pass
    return dedupe_preserve_order([query, *queries])[: max(settings.rag_multi_query_count, 1) + 1]


def generate_hypothetical_document(query: str, llm: AgentLLM | None = None) -> str:
    settings = get_settings()
    if not settings.enable_hyde:
        return ""
    if settings.hyde_question_only and not looks_like_question(query):
        return ""
    try:
        llm_client = llm or AgentLLM()
        prompt = HYDE_PROMPT.format(query=query)
        response = llm_client.think([{"role": "user", "content": prompt}], temperature=0.2, silent=True)
        return (response or "").strip()
    except Exception:
        return ""
