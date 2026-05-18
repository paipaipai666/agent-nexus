"""memory_search tool — allows agents to actively query long-term memory."""

from agentnexus.memory.long_term import LongTermMemory
from agentnexus.rag.chroma_client import get_embedding_model


def memory_search(query: str, category: str = "") -> str:
    """Search long-term memory for relevant past interactions, user preferences, or facts.

    Args:
        query: Search query in natural language.
        category: Optional filter (user_preference / entity_fact / conclusion).

    Returns:
        Formatted search results, or a message if nothing found.
    """
    ltm = LongTermMemory()
    model = get_embedding_model()
    try:
        embedding = model.encode(query, normalize_embeddings=True).tolist()
    except Exception:
        return "[memory_search] 嵌入模型不可用"

    cat = category if category else None
    results = ltm.search(query_embedding=embedding, category=cat, limit=3, min_similarity=0.35)
    if not results:
        return "[memory_search] 未找到相关记忆"

    lines = ["相关记忆:"]
    for r in results:
        lines.append(f"- [{r['category']}] {r['content']} (重要性: {r['importance']:.1f})")
    return "\n".join(lines)
