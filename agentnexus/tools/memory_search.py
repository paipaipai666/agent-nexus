"""memory_search tool — allows agents to actively query long-term memory."""

from agentnexus.core.llm import AgentLLM
from agentnexus.memory.long_term import get_long_term_memory
from agentnexus.rag.embeddings import get_embedding_model

_QUERY_REWRITE_PROMPT = """\
将以下用户查询改写为3-5个关键词或短语，用空格分隔，用于向量搜索。提取核心概念和人名/地名/专有名词，去除语气词和冗余描述。只输出关键词。

查询: {query}
关键词:"""

# Keywords that indicate the query is about a personal entity fact (name, age, etc.)
_ENTITY_QUERY_KEYWORDS = frozenset([
    "名字", "姓名", "叫什么", "几岁", "多大", "哪里人", "在哪工作",
    "住在", "年龄", "生日", "电话", "邮箱", "地址", "职业", "学校",
    "哪个公司", "做什么工作", "什么职业", "哪个城市",
])

# Category migration map (old → new)
_CATEGORY_MIGRATION = {
    "entity_fact": "fact", "conclusion": "fact",
    "user_preference": "preference", "tool_preference": "preference",
    "task_progress": "note", "error_pattern": "note", "conversation": "note",
}

_MAX_CONTENT_TOKENS = 200  # ~800 chars for Chinese text


def _is_entity_query(query: str) -> bool:
    """Detect if a query is asking about personal/entity information."""
    return any(kw in query for kw in _ENTITY_QUERY_KEYWORDS)


def _truncate_content(text: str, max_tokens: int = _MAX_CONTENT_TOKENS) -> str:
    """Truncate content to fit within token budget. Chinese ~4 chars/token."""
    max_chars = max_tokens * 4
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "..."


def _diversify_results(results: list[dict], limit: int = 5) -> list[dict]:
    """Ensure category diversity: at least 1 from each category if available.
    Then fill remaining slots by score."""
    if len(results) <= limit:
        return results

    by_category: dict[str, list[dict]] = {}
    for r in results:
        cat = r.get("category", "unknown")
        by_category.setdefault(cat, []).append(r)

    # Pick top-1 from each category first
    diversified = []
    for cat_results in by_category.values():
        if cat_results:
            diversified.append(cat_results[0])

    # Fill remaining by score (skip already picked)
    seen_ids = {r.get("id") for r in diversified}
    remaining = [r for r in results if r.get("id") not in seen_ids]
    remaining.sort(key=lambda r: r.get("_score", 0), reverse=True)

    diversified.extend(remaining)
    return diversified[:limit]


def _rewrite_query(query: str) -> str:
    """Use a lightweight LLM call to extract search-optimized keywords."""
    try:
        llm = AgentLLM()
        prompt = _QUERY_REWRITE_PROMPT.format(query=query)
        rewritten = llm.think([{"role": "user", "content": prompt}], silent=True)
        if rewritten and len(rewritten.strip()) >= 2:
            return rewritten.strip()
    except Exception:
        pass
    return query


def memory_search(query: str, category: str = "") -> str:
    """Search long-term memory for relevant past interactions, user preferences, or facts.

    Args:
        query: Search query in natural language — will be rewritten for better matching.
        category: Optional filter (fact / preference / note). Legacy names auto-migrated.

    Returns:
        Formatted search results with similarity scores, or a message if nothing found.
    """
    ltm = get_long_term_memory()
    model = get_embedding_model()

    # Rewrite query for better embedding match
    search_query = _rewrite_query(query)

    try:
        raw = model.encode(search_query, normalize_embeddings=True)
        embedding = raw.tolist() if hasattr(raw, "tolist") else list(raw)
    except Exception as e:
        return f"[memory_search] 嵌入模型不可用: {e}"

    cat = _CATEGORY_MIGRATION.get(category, category) if category else None
    min_sim = 0.25

    results = ltm.search(query_embedding=embedding, category=cat, limit=5, min_similarity=min_sim)
    if not results:
        return "[memory_search] 未找到相关记忆"

    # Entity query boost: if query is about personal info, ensure fact category is represented
    if _is_entity_query(query) and not category:
        has_fact = any(r["category"] in ("fact", "entity_fact") for r in results)
        if not has_fact:
            fact_results = ltm.search(
                query_embedding=embedding, category="fact",
                limit=3, min_similarity=min_sim,
            )
            if fact_results:
                seen_ids = set()
                merged = []
                for r in fact_results + results:
                    rid = r.get("id")
                    if rid not in seen_ids:
                        seen_ids.add(rid)
                        merged.append(r)
                results = merged

    # Diversify: ensure at least 1 result per category
    results = _diversify_results(results, limit=5)

    # Format output with token budget
    lines = [f"相关记忆 (查询: '{search_query}'):"]
    for r in results:
        sim = r.get("_score", 0)
        star = _score_stars(sim)
        cat_display = _CATEGORY_MIGRATION.get(r['category'], r['category'])
        content = _truncate_content(r['content'])
        lines.append(f"- {star} [{cat_display}] {content}")
    return "\n".join(lines)


def _score_stars(score: float) -> str:
    if score >= 0.7:
        return "★★★"
    elif score >= 0.5:
        return "★★☆"
    else:
        return "★☆☆"
