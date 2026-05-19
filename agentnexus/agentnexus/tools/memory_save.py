"""memory_save tool — allows agents to proactively save facts to long-term memory."""

from agentnexus.memory.long_term import LongTermMemory
from agentnexus.rag.chroma_client import get_embedding_model

_VALID_CATEGORIES = {
    "user_preference", "entity_fact", "conclusion",
    "task_progress", "error_pattern", "tool_preference",
}


def memory_save(content: str, category: str = "entity_fact", importance: float = 0.7) -> str:
    """Save a fact, preference, or conclusion to long-term memory for future recall.

    Use this when the user explicitly shares personal info (name, preferences, background),
    or when you discover important facts that should be remembered across sessions.

    Args:
        content: The fact to remember, written as a clear standalone sentence.
        category: Type of memory. One of: user_preference, entity_fact, conclusion,
                  task_progress, error_pattern, tool_preference.
        importance: How important this memory is (0.0-1.0). Default 0.7.

    Returns:
        Confirmation message.
    """
    if not content or len(content.strip()) < 5:
        return "[memory_save] 内容太短，至少需要5个字符"

    if category not in _VALID_CATEGORIES:
        return f"[memory_save] 无效分类 '{category}'，有效值: {', '.join(sorted(_VALID_CATEGORIES))}"

    importance = max(0.0, min(1.0, importance))

    ltm = LongTermMemory()
    model = get_embedding_model()

    try:
        embedding = model.encode(content, normalize_embeddings=True).tolist()
    except Exception:
        # Save without embedding — will be re-embedded on next search
        embedding = []

    ltm.save(
        session_id="agent_written",
        content=content.strip(),
        category=category,
        importance=importance,
        embedding=embedding,
    )
    return f"[memory_save] 已保存 [{category}] {content.strip()[:100]}"
