"""Periodic Reflection — distill higher-level patterns from recent memories.

Runs periodically (or on-demand) to review recent note-category memories,
identify recurring patterns, and save distilled insights as fact/preference memories.

Original note memories are marked as reflected (superseded_by → new pattern memory)
so they aren't re-processed.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

_REFLECTION_PROMPT = """\
你是记忆反思助手。请分析以下近期记忆条目，从中归纳出高阶模式、反复出现的偏好、或重要的跨对话结论。

记忆条目（按时间顺序）:
{memories}

请输出 JSON，格式如下:
{{
  "patterns": [
    {{"content": "归纳出的模式或偏好描述", "category": "fact 或 preference", "importance": 0.0-1.0}}
  ]
}}

要求:
- 只归纳确实反复出现或有明确证据的模式，不要猜测
- 如果没有值得归纳的模式，返回空数组
- category 只能是 "fact" 或 "preference"
- 每条 pattern 应该是独立、完整的陈述句
- importance 根据模式的显著程度打分（0.7-0.95）"""


def _format_memories_for_prompt(memories: list[dict]) -> str:
    """Format memories into a readable list for the LLM prompt."""
    lines = []
    for i, m in enumerate(memories, 1):
        cat = m.get("category", "?")
        content = m.get("content", "")[:200]  # truncate for prompt
        lines.append(f"{i}. [{cat}] {content}")
    return "\n".join(lines)


def _should_reflect(memories: list[dict]) -> bool:
    """Pre-filter: only reflect if there are enough memories to find patterns."""
    # Need at least 5 note-type memories to find meaningful patterns
    note_count = sum(1 for m in memories if m.get("category") in ("note", "task_progress", "error_pattern", "conversation"))
    return note_count >= 5


def run_reflection(
    *,
    llm: Any,
    embed_model: Any,
    long_term: Any,
    session_id: str = "reflection",
    days: int = 7,
    max_memories: int = 50,
) -> dict:
    """Run periodic reflection on recent memories.

    1. Fetch recent note-category memories from the last N days
    2. Use LLM to identify higher-level patterns
    3. Save distilled patterns as fact/preference memories
    4. Mark original note memories as reflected

    Returns:
        {"patterns_found": int, "patterns_saved": int, "memories_reviewed": int}
    """
    if not long_term:
        return {"patterns_found": 0, "patterns_saved": 0, "memories_reviewed": 0, "error": "No LTM available"}

    # Fetch recent note-category memories (not already reflected)
    rows = long_term._conn.execute(
        "SELECT id, category, content, importance, access_count, created_at "
        "FROM long_term_memories "
        "WHERE category IN ('note', 'task_progress', 'error_pattern', 'conversation') "
        "AND superseded_by IS NULL "
        "AND datetime(created_at) > datetime('now', ?) "
        "ORDER BY created_at ASC LIMIT ?",
        (f"-{days} days", max_memories),
    ).fetchall()

    memories = [dict(r) for r in rows]
    if not _should_reflect(memories):
        return {"patterns_found": 0, "patterns_saved": 0, "memories_reviewed": len(memories),
                "reason": f"Not enough note memories ({len(memories)}) to find patterns"}

    # Send to LLM for pattern extraction
    prompt = _REFLECTION_PROMPT.format(memories=_format_memories_for_prompt(memories))
    try:
        response = llm.think([{"role": "user", "content": prompt}], silent=True) or "{}"
        data = json.loads(response.strip().lstrip("```json").rstrip("```").strip())
    except Exception as e:
        logger.warning("Reflection LLM call failed: %s", e)
        return {"patterns_found": 0, "patterns_saved": 0, "memories_reviewed": len(memories),
                "error": str(e)}

    patterns = data.get("patterns", [])
    if not patterns:
        return {"patterns_found": 0, "patterns_saved": 0, "memories_reviewed": len(memories)}

    # Save each pattern as a new memory and mark originals as reflected
    saved_count = 0
    memory_ids = [m["id"] for m in memories]

    for p in patterns:
        content = p.get("content", "").strip()
        if not content or len(content) < 10:
            continue
        category = p.get("category", "fact")
        if category not in ("fact", "preference"):
            category = "fact"
        importance = max(0.7, min(0.95, float(p.get("importance", 0.8))))

        # Embed the pattern
        try:
            raw = embed_model.encode(content, normalize_embeddings=True)
            vec = raw.tolist() if hasattr(raw, "tolist") else list(raw)
        except Exception:
            vec = []

        # Semantic dedup: skip if pattern already exists
        if vec:
            existing = long_term.search(query_embedding=vec, limit=1, min_similarity=0.90)
            if existing and existing[0].get("_score", 0) >= 0.90:
                logger.debug("Skipping duplicate pattern (sim=%.2f): %s", existing[0]["_score"], content[:60])
                continue

        # Save pattern
        new_id = long_term.save(
            session_id=session_id,
            content=f"[Reflection] {content}",
            category=category,
            importance=importance,
            embedding=vec,
        )
        saved_count += 1

        # Mark original note memories as reflected (superseded_by → new pattern)
        if new_id:
            for mid in memory_ids:
                long_term.mark_superseded(mid, new_id)

    result = {
        "patterns_found": len(patterns),
        "patterns_saved": saved_count,
        "memories_reviewed": len(memories),
    }
    logger.info("Reflection complete: %d patterns saved from %d memories", saved_count, len(memories))
    return result
