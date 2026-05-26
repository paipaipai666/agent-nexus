"""memory_search tool — allows agents to actively query long-term memory."""

from agentnexus.core.llm import AgentLLM
from agentnexus.memory.long_term import get_long_term_memory
from agentnexus.rag.embeddings import get_embedding_model

_QUERY_REWRITE_PROMPT = """\
将以下用户查询改写为3-5个关键词或短语，用空格分隔，用于向量搜索。提取核心概念和人名/地名/专有名词，去除语气词和冗余描述。只输出关键词。

查询: {query}
关键词:"""


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
        category: Optional filter (user_preference / entity_fact / conclusion /
                  task_progress / error_pattern / tool_preference).

    Returns:
        Formatted search results with similarity scores, or a message if nothing found.
    """
    ltm = get_long_term_memory()
    model = get_embedding_model()

    # Rewrite query for better embedding match
    search_query = _rewrite_query(query)

    try:
        embedding = model.encode(search_query, normalize_embeddings=True).tolist()
    except Exception:
        return "[memory_search] 嵌入模型不可用"

    cat = category if category else None
    results = ltm.search(query_embedding=embedding, category=cat, limit=5, min_similarity=0.35)
    if not results:
        return "[memory_search] 未找到相关记忆"

    lines = [f"相关记忆 (查询: '{search_query}'):"]
    for r in results:
        sim = r.get("_score", 0)
        star = _score_stars(sim)
        lines.append(
            f"- {star} [{r['category']}] {r['content']}"
        )
    return "\n".join(lines)


def _score_stars(score: float) -> str:
    if score >= 0.7:
        return "★★★"
    elif score >= 0.5:
        return "★★☆"
    else:
        return "★☆☆"
