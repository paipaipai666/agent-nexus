"""memory_search tool — allows agents to actively query long-term memory."""

from agentnexus.core.modules import get_module_host
from agentnexus.memory.extraction import _parse_context


def get_long_term_memory():
    """Resolve the selected provider for standalone tool calls."""
    return get_module_host().optional("long_term")

_ENTITY_QUERY_KEYWORDS = frozenset([
    "名字", "姓名", "叫什么", "几岁", "多大", "哪里人", "在哪工作",
    "住在", "年龄", "生日", "电话", "邮箱", "地址", "职业", "学校",
    "哪个公司", "做什么工作", "什么职业", "哪个城市",
])

_CATEGORY_MIGRATION = {
    "entity_fact": "fact", "conclusion": "fact",
    "user_preference": "preference", "tool_preference": "preference",
    "task_progress": "note", "error_pattern": "note", "conversation": "note",
}

_MAX_CONTENT_TOKENS = 200
_MAX_CONTEXT_TOKENS = 80


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

    diversified = []
    for cat_results in by_category.values():
        if cat_results:
            diversified.append(cat_results[0])

    seen_ids = {r.get("id") for r in diversified}
    remaining = [r for r in results if r.get("id") not in seen_ids]
    remaining.sort(key=lambda r: r.get("_score", 0), reverse=True)

    diversified.extend(remaining)
    return diversified[:limit]


def memory_search(query: str, category: str = "", *, modules=None) -> str:
    """Search long-term memory for relevant past interactions, user preferences, or facts.

    Args:
        query: Search query in natural language — will be rewritten for better matching.
        category: Optional filter (fact / preference / note). Legacy names auto-migrated.

    Returns:
        Formatted search results with similarity scores, or a message if nothing found.
    """
    ltm = modules.optional("long_term") if modules is not None else get_long_term_memory()
    if ltm is None:
        return "[memory_search] 长期记忆不可用"
    search_query = query

    cat = _CATEGORY_MIGRATION.get(category, category) if category else None
    min_sim = 0.25

    try:
        results = ltm.search_text(search_query, category=cat, limit=5,
                                  min_similarity=min_sim, rewrite_query=True)
    except Exception as exc:
        return f"[memory_search] 记忆检索失败: {exc}"
    if not results:
        return "[memory_search] 未找到相关记忆"

    if _is_entity_query(query) and not category:
        has_fact = any(r["category"] in ("fact", "entity_fact") for r in results)
        if not has_fact:
            fact_results = ltm.search_text(
                search_query, category="fact",
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

    results = _diversify_results(results, limit=5)

    lines = [f"相关记忆 (查询: '{search_query}'):"]
    for r in results:
        sim = r.get("_score", 0)
        star = _score_stars(sim)
        cat_display = _CATEGORY_MIGRATION.get(r['category'], r['category'])
        content = _truncate_content(r['content'])
        lines.append(f"- {star} [{cat_display}] {content}")
        ctx = _parse_context(r.get("metadata_json"))
        if ctx:
            lines.append(f"    └ 来源: {_truncate_content(ctx, _MAX_CONTEXT_TOKENS)}")
    return "\n".join(lines)


def _score_stars(score: float) -> str:
    if score >= 0.7:
        return "★★★"
    elif score >= 0.5:
        return "★★☆"
    else:
        return "★☆☆"
