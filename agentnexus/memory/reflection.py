"""Periodic Curator — batch-distill durable memories off the hot path.

Reviews recent note-category memories on demand (or on a schedule), uses one
LLM call over the whole batch to propose higher-level patterns, and writes the
proposals to the ``pending_memories`` quarantine table — nothing enters LTM or
project files without explicit user approval (POST /memory/pending/{id}/approve
or ``memory approve`` CLI).

Originals are NOT superseded at proposal time; the pending row carries
``source_ids`` and approval performs the supersession.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

_REFLECTION_PROMPT = """\
你是记忆整理助手。请分析以下近期记忆条目，归纳出值得长期保留的模式、反复出现的偏好、或重要结论。

记忆条目（按时间顺序）:
{memories}

请输出 JSON，格式如下:
{{
  "patterns": [
    {{
      "content": "归纳出的模式或结论（独立完整的陈述句）",
      "scope": "user 或 project",
      "category": "fact 或 preference（scope=user 时填写）",
      "kind": "memo、decision 或 lesson（scope=project 时填写）",
      "importance": 0.0-1.0
    }}
  ]
}}

要求:
- 只归纳确实反复出现或有明确证据的模式，不要猜测
- 如果没有值得归纳的模式，返回空数组
- scope 判断：用户画像、跨项目通用的偏好/事实 → user；只与某个代码库/项目相关的知识 → project
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

    # Quarantine: proposals go to pending_memories, never directly to LTM.
    proposed_count = 0
    memory_ids = [m["id"] for m in memories]

    for p in patterns:
        content = p.get("content", "").strip()
        if not content or len(content) < 10:
            continue
        scope = p.get("scope", "user")
        if scope not in ("user", "project"):
            scope = "user"
        category = p.get("category", "fact")
        if category not in ("fact", "preference"):
            category = "fact"
        kind = p.get("kind", "")
        if kind not in ("memo", "decision", "lesson"):
            kind = "memo"
        importance = max(0.7, min(0.95, float(p.get("importance", 0.8))))

        # Semantic dedup against LTM: skip if the pattern is already stored
        try:
            raw = embed_model.encode(content, normalize_embeddings=True)
            vec = raw.tolist() if hasattr(raw, "tolist") else list(raw)
        except Exception:
            vec = []
        if vec:
            existing = long_term.search(query_embedding=vec, limit=1, min_similarity=0.90)
            if existing and existing[0].get("_score", 0) >= 0.90:
                logger.debug("Skipping duplicate pattern (sim=%.2f): %s", existing[0]["_score"], content[:60])
                continue

        # Project-scope proposals need a workspace: take it from the source notes' sessions
        workspace_path = ""
        if scope == "project":
            workspace_path = _workspace_for_memories(long_term, memories) or ""
            if not workspace_path:
                scope = "user"  # can't place it — fall back to user review

        pending_id = long_term.add_pending(
            content,
            scope=scope,
            category=category,
            kind=kind,
            workspace_path=workspace_path,
            importance=importance,
            source="curator",
            source_ids=memory_ids,
        )
        if pending_id is not None:
            proposed_count += 1

    result = {
        "patterns_found": len(patterns),
        "patterns_saved": 0,
        "patterns_proposed": proposed_count,
        "memories_reviewed": len(memories),
    }
    logger.info("Curator complete: %d patterns proposed from %d memories", proposed_count, len(memories))
    return result


def _workspace_for_memories(long_term: Any, memories: list[dict]) -> str | None:
    """Resolve the workspace owning the source memories (same SQLite db)."""
    session_ids = [m.get("session_id") for m in memories if m.get("session_id")]
    if not session_ids:
        return None
    placeholders = ",".join("?" for _ in session_ids)
    try:
        row = long_term._conn.execute(
            f"SELECT workspace_path, MAX(updated_at) AS latest "
            f"FROM conversation_sessions WHERE session_id IN ({placeholders}) "
            f"AND workspace_path != '' GROUP BY workspace_path ORDER BY latest DESC LIMIT 1",
            session_ids,
        ).fetchone()
    except Exception:
        return None
    return row["workspace_path"] if row else None
