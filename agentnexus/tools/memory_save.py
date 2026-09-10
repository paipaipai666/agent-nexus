"""memory_save tool — allows agents to proactively save facts to long-term memory."""

from agentnexus.memory.extraction import _embed_text
from agentnexus.memory.long_term import get_long_term_memory
from agentnexus.rag.embeddings import get_embedding_model

_VALID_CATEGORIES = {
    "fact", "preference", "note",
    # Legacy names — accepted and auto-migrated
    "user_preference", "entity_fact", "conclusion",
    "task_progress", "error_pattern", "tool_preference",
}

# Map old names to new
_CATEGORY_MIGRATION = {
    "entity_fact": "fact",
    "conclusion": "fact",
    "user_preference": "preference",
    "tool_preference": "preference",
    "task_progress": "note",
    "error_pattern": "note",
    "conversation": "note",
}


def memory_save(content: str, category: str = "fact", importance: float = 0.85,
                context: str = "", scope: str = "user", kind: str = "memo",
                tags: str = "") -> str:
    """Save a fact, preference, or conclusion to long-term memory for future recall.

    Use this when the user explicitly shares personal info (name, preferences, background),
    or when you discover important facts that should be remembered across sessions.

    Args:
        content: The fact to remember, written as a clear standalone sentence.
        category: Type of memory. One of: fact, preference, note.
        importance: How important this memory is (0.0-1.0). Default 0.85.
        context: Optional one-sentence rationale describing the scene or evidence
            that produced this conclusion (e.g. "推荐歌曲时用户否定了莫文蔚、选择周杰伦").
            Broadens recall and helps distinguish same-scene conflicts from
            different-scene coexisting preferences. Do not include personal info.
        scope: "user" (default) — cross-project memory in the global store;
            "project" — plain-text file under the current project's .agentnexus/.
        kind: Project scope only. One of: memo (commands/conventions/landmines),
            decision (decision + rationale), lesson (pitfall), log (work log entry).
        tags: Project scope only (memo/decision/lesson). Space-separated tags,
            stored as #tag for grep-ability.

    Returns:
        Confirmation message.
    """
    if not content or len(content.strip()) < 5:
        return "[memory_save] 内容太短，至少需要5个字符"

    if scope == "project":
        from agentnexus.memory.project import ProjectMemory
        from agentnexus.tools.workspace import get_effective_workspace

        if kind not in ("memo", "decision", "lesson", "log"):
            return f"[memory_save] 无效 kind '{kind}'，有效值: memo, decision, lesson, log"
        try:
            pm = ProjectMemory(get_effective_workspace())
            if kind == "log":
                pm.log_work(content.strip())
            else:
                pm.add_entry(kind, content.strip(), tags=tags)
        except Exception as e:
            return f"[memory_save] 项目记忆写入失败: {e}"
        return f"[memory_save] 已保存到项目记忆 [{kind}] {content.strip()[:100]}"
    if scope != "user":
        return f"[memory_save] 无效 scope '{scope}'，有效值: user, project"

    if category not in _VALID_CATEGORIES:
        return f"[memory_save] 无效分类 '{category}'，有效值: fact, preference, note"

    # Migrate legacy category names
    category = _CATEGORY_MIGRATION.get(category, category)
    importance = max(0.0, min(1.0, importance))
    context = (context or "").strip()

    ltm = get_long_term_memory()
    model = get_embedding_model()

    try:
        raw = model.encode(_embed_text(content, context), normalize_embeddings=True)
        embedding = raw.tolist() if hasattr(raw, "tolist") else list(raw)
    except Exception:
        # Save without embedding — will be re-embedded on next search
        embedding = []

    save_kwargs: dict = {
        "session_id": "agent_written",
        "content": content.strip(),
        "category": category,
        "importance": importance,
        "embedding": embedding,
    }
    if context:
        save_kwargs["metadata"] = {"context": context}
    ltm.save(**save_kwargs)
    return f"[memory_save] 已保存 [{category}] {content.strip()[:100]}"


def memory_project_status() -> str:
    """Return the current project's memory index and state (.agentnexus/).

    Use this to review what project-level knowledge has been recorded.
    """
    from agentnexus.memory.project import ProjectMemory
    from agentnexus.tools.workspace import get_effective_workspace

    try:
        pm = ProjectMemory(get_effective_workspace())
    except Exception as e:
        return f"[memory_project_status] 项目记忆不可用: {e}"
    ctx = pm.format_context()
    if not ctx:
        return f"[memory_project_status] 项目记忆为空 ({pm.root})"
    return ctx
