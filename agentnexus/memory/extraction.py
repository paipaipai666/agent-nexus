"""Long-term memory extraction service."""

from __future__ import annotations

import json
import re
from typing import Any

from agentnexus.prompts import load_prompt
from agentnexus.rag.embeddings import embedding_to_list

EXTRACT_PROMPT = load_prompt("memory_extract")

# Phase 1: merged from 6 categories → 3
# fact: entity facts + conclusions (permanent, high importance)
# preference: user preferences + tool preferences (permanent, high importance)
# note: task progress + error patterns + conversation context (temporary, medium importance)
MEMORY_CATEGORIES = {
    "fact": 0.85,
    "preference": 0.9,
    "note": 0.7,
}

CATEGORY_LABELS = {
    "fact": "事实",
    "preference": "偏好",
    "note": "笔记",
}

# Backward-compatible mapping from old categories to new
_CATEGORY_MIGRATION = {
    "entity_fact": "fact",
    "conclusion": "fact",
    "user_preference": "preference",
    "tool_preference": "preference",
    "task_progress": "note",
    "error_pattern": "note",
    "conversation": "note",
}


def migrate_category(cat: str) -> str:
    """Map old 6-category names to new 3-category names."""
    return _CATEGORY_MIGRATION.get(cat, cat)


def extract_xml_tag(text: str, tag: str) -> str | None:
    pattern = rf"<{tag}>\s*(.*?)\s*</{tag}>"
    match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
    return match.group(1) if match else None


def parse_memory_payload(response: str) -> dict:
    try:
        return json.loads(response.strip().lstrip("```json").rstrip("```").strip())
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("Failed to parse memory extraction response: %s", e)
        return {}


def iter_memory_items(data: dict):
    for category, importance in MEMORY_CATEGORIES.items():
        for item in data.get(category, []):
            if isinstance(item, dict):
                item = item.get("content") or item.get("text") or ""
            if not isinstance(item, str) or len(item.strip()) < 5:
                continue
            yield category, importance, item.strip()


_CONFLICT_PROMPT = """判断以下两条记忆是否矛盾（信息冲突、互相排斥）。只回答 "矛盾" 或 "不矛盾"。

已有记忆: {old}
新记忆: {new}
判断:"""


def _check_conflict(llm: Any, old_content: str, new_content: str) -> bool:
    """Use LLM to check if two memories contradict each other."""
    try:
        prompt = _CONFLICT_PROMPT.format(old=old_content, new=new_content)
        result = llm.think([{"role": "user", "content": prompt}], silent=True)
        return "矛盾" in (result or "")
    except Exception:
        return False


def extract_and_save_memories(
    *,
    llm: Any,
    embed_model: Any,
    long_term: Any,
    session_id: str,
    question: str,
    answer: str,
) -> None:
    import logging
    logger = logging.getLogger(__name__)

    prompt = EXTRACT_PROMPT.format(question=question, answer=answer)
    response = llm.think([{"role": "user", "content": prompt}], silent=True) or "{}"
    data = parse_memory_payload(response)
    saved_count = 0
    for category, importance, item in iter_memory_items(data):
        # Use LLM-provided importance if available, else fall back to category default
        item_importance = importance
        if isinstance(data.get(category), list):
            for entry in data[category]:
                if isinstance(entry, dict):
                    content = entry.get("content") or entry.get("text") or ""
                    if content.strip() == item and "importance" in entry:
                        try:
                            item_importance = max(0.0, min(1.0, float(entry["importance"])))
                        except (ValueError, TypeError):
                            pass
                        break

        vec = embedding_to_list(embed_model.encode(item, normalize_embeddings=True))

        # Semantic dedup: skip if a very similar memory already exists
        if long_term and vec:
            existing = long_term.search(query_embedding=vec, limit=1, min_similarity=0.90)
            if existing and existing[0].get("_score", 0) >= 0.90:
                logger.debug("Skipping duplicate memory (similarity=%.2f): %s",
                             existing[0]["_score"], item[:80])
                continue

        # Conflict detection for fact/preference categories
        if long_term and vec and category in ("fact", "preference"):
            candidates = long_term.search(
                query_embedding=vec, category=category,
                limit=3, min_similarity=0.70,
            )
            for c in candidates:
                if c.get("_score", 0) >= 0.90:
                    continue  # already handled by dedup above
                if _check_conflict(llm, c["content"], item):
                    logger.info("Conflict detected: '%s' vs '%s', superseding old", item[:40], c["content"][:40])
                    new_id = long_term.save(
                        session_id=session_id,
                        content=item,
                        category=category,
                        importance=item_importance,
                        embedding=vec,
                    )
                    long_term.mark_superseded(c["id"], new_id)
                    saved_count += 1
                    break
            else:
                # No conflict — save normally
                long_term.save(
                    session_id=session_id,
                    content=item,
                    category=category,
                    importance=item_importance,
                    embedding=vec,
                )
                saved_count += 1
        else:
            long_term.save(
                session_id=session_id,
                content=item,
                category=category,
                importance=item_importance,
                embedding=vec,
            )
            saved_count += 1
    if saved_count:
        logger.debug("Extracted and saved %d memories from conversation", saved_count)
