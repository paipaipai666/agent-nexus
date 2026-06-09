"""Long-term memory extraction service."""

from __future__ import annotations

import json
import logging
import re
import threading
from typing import Any

from agentnexus.core.pii import contains_pii, mask_pii
from agentnexus.memory.metrics import get_metrics
from agentnexus.prompts import load_prompt
from agentnexus.rag.embeddings import embedding_to_list

logger = logging.getLogger(__name__)

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
        text = response.strip()
        text = re.sub(r'^```(?:json)?\s*\n?', '', text)
        text = re.sub(r'\n?```\s*$', '', text)
        return json.loads(text.strip())
    except Exception as e:
        logger.warning("Failed to parse memory extraction response: %s", e)
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


# ── module-level lock for the extraction pipeline ───────────────────
_extraction_lock = threading.Lock()


def extract_and_save_memories(
    *,
    llm: Any,
    embed_model: Any,
    long_term: Any,
    session_id: str,
    question: str,
    answer: str,
) -> None:
    metrics = get_metrics()
    metrics.incr("extraction_attempts")

    # ── Segment 1: LLM extraction (unlocked — slow network call) ────
    prompt = EXTRACT_PROMPT.format(question=question, answer=answer)
    response = llm.think([{"role": "user", "content": prompt}], silent=True) or "{}"
    data = parse_memory_payload(response)
    saved_count = 0

    for category, importance, item in iter_memory_items(data):
        if not item:
            continue

        # ── PII fallback: regex scan after LLM extraction ───────────
        # Prompt tells LLM not to extract PII, but LLM is non-deterministic.
        # This catches phone numbers, emails, IDs that slipped through.
        if contains_pii(item):
            item = mask_pii(item)
            metrics.incr("pii_masked_count")
            logger.warning("PII detected in extracted memory (source control bypassed), masked: %s", item[:60])

        # ── Segment 1.5: Embedding generation (unlocked — slow compute/API) ──
        vec = embedding_to_list(embed_model.encode(item, normalize_embeddings=True))

        # ── Segment 2: Dedup query (locked — fast DB read) ──────────
        with _extraction_lock:
            if vec:
                existing = long_term.search(query_embedding=vec, limit=3, min_similarity=0.90)
                if existing:
                    metrics.incr("writes_skipped_dedup")
                    logger.debug("Skipping duplicate memory: %s", item[:80])
                    continue

        # ── Segment 3: Conflict detection (unlocked — LLM call) ─────
        conflict_ids: list[int] = []
        # All categories get conflict detection (note threshold is slightly higher)
        threshold = 0.75 if category == "note" else 0.70
        with _extraction_lock:
            candidates = long_term.search(
                query_embedding=vec, category=category,
                limit=3, min_similarity=threshold,
            ) if vec else []
        # LLM conflict check outside lock
        for c in candidates:
            if c.get("_score", 0) >= 0.90:
                continue  # already handled by dedup
            if _check_conflict(llm, c["content"], item):
                conflict_ids.append(c["id"])

        # ── Segment 4: Double-check + save (locked — fast DB write) ─
        with _extraction_lock:
            # Double-check: another thread may have written a duplicate
            if vec:
                recheck = long_term.search(query_embedding=vec, limit=1, min_similarity=0.90)
                if recheck:
                    metrics.incr("writes_skipped_dedup")
                    continue

            new_id = long_term.save(
                session_id=session_id,
                content=item,
                category=category,
                importance=importance,
                embedding=vec,
            )
            # Supersede all conflicting old memories (idempotent)
            for cid in conflict_ids:
                long_term.mark_superseded(cid, new_id)

            metrics.incr("writes_total")
            if conflict_ids:
                metrics.incr("conflicts_detected", len(conflict_ids))
                metrics.incr("superseded_count", len(conflict_ids))
                logger.info("Conflict detected: '%s' superseded %d old memories", item[:40], len(conflict_ids))
            saved_count += 1

    if saved_count:
        metrics.incr("extraction_successes")
        logger.debug("Extracted and saved %d memories from conversation", saved_count)
