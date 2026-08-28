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


def _embed_text(content: str, context: str | None) -> str:
    """Build the text used for embedding a memory.

    When a context (one-sentence rationale) is present, concatenate it so the
    vector captures the scene/evidence behind the conclusion — this broadens
    recall (a query about the scene can match a short conclusion). Without
    context, embed the conclusion alone (preserves behavior for legacy rows
    and context-free memories).
    """
    if context:
        return f"{content}\n{context}"
    return content


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
    """Yield (category, importance, content, context) tuples from parsed payload.

    Items may be plain strings (legacy format, context="") or dicts with
    "content"/"text" and an optional "context" (one-sentence rationale).
    """
    for category, importance in MEMORY_CATEGORIES.items():
        for item in data.get(category, []):
            context = ""
            if isinstance(item, dict):
                content = item.get("content") or item.get("text") or ""
                context = (item.get("context") or "").strip()
            else:
                content = item
            if not isinstance(content, str) or len(content.strip()) < 5:
                continue
            yield category, importance, content.strip(), context


_CONFLICT_PROMPT = """判断以下两条记忆是否矛盾（信息冲突、互相排斥）。只回答 "矛盾" 或 "不矛盾"。

判断要点：
- 如果两条记忆的结论互相排斥，且属于同一场景/同一维度，回答 "矛盾"。
- 如果结论看似相反，但 context 表明它们来自不同场景（例如不同任务、不同上下文下的偏好），它们可以并存，回答 "不矛盾"。
- context 缺失时，仅依据结论判断。

已有记忆: {old}
已有记忆来源: {old_context}
新记忆: {new}
新记忆来源: {new_context}
判断:"""


def _check_conflict(
    llm: Any,
    old_content: str,
    new_content: str,
    old_context: str = "",
    new_context: str = "",
) -> bool:
    """Use LLM to check if two memories contradict each other.

    Contexts (one-sentence rationales) are included so the model can tell
    "same-scene contradiction" (real conflict → supersede) from
    "different-scene coexisting preferences" (not conflict → keep both).
    Returns True only on a genuine conflict; LLM failure assumes no conflict.
    """
    try:
        prompt = _CONFLICT_PROMPT.format(
            old=old_content,
            old_context=old_context or "（无）",
            new=new_content,
            new_context=new_context or "（无）",
        )
        result = llm.think([{"role": "user", "content": prompt}], silent=True) or ""
        # Exact match, not substring: "不矛盾" contains "矛盾", so a substring
        # check would treat every non-conflict answer as a conflict.
        return result.strip() == "矛盾"
    except Exception:
        logger.debug("Conflict check failed, assuming no conflict", exc_info=True)
        return False


def _parse_context(metadata_json: Any) -> str:
    """Extract the context string from a memory row's metadata_json field.

    metadata_json is a JSON string (or None / already-parsed dict in tests).
    Returns "" when absent or malformed — never raises.
    """
    if not metadata_json:
        return ""
    if isinstance(metadata_json, dict):
        ctx = metadata_json.get("context")
        return ctx.strip() if isinstance(ctx, str) else ""
    try:
        data = json.loads(metadata_json)
    except (ValueError, TypeError):
        return ""
    if not isinstance(data, dict):
        return ""
    ctx = data.get("context")
    return ctx.strip() if isinstance(ctx, str) else ""


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

    for category, importance, item, context in iter_memory_items(data):
        if not item:
            continue

        # ── PII fallback: regex scan after LLM extraction ───────────
        # Prompt tells LLM not to extract PII, but LLM is non-deterministic.
        # This catches phone numbers, emails, IDs that slipped through.
        # Context is masked too — it gets embedded and fed into the conflict
        # prompt, so it must not carry PII either.
        if contains_pii(item):
            item = mask_pii(item)
            metrics.incr("pii_masked_count")
            logger.warning("PII detected in extracted memory (source control bypassed), masked: %s", item[:60])
        if context and contains_pii(context):
            context = mask_pii(context)
            metrics.incr("pii_masked_count")
            logger.warning("PII detected in extracted memory context, masked: %s", context[:60])

        # ── Segment 1.5: Embedding generation (unlocked — slow compute/API) ──
        # D: embed content + context so the scene/evidence broadens recall.
        vec = embedding_to_list(embed_model.encode(_embed_text(item, context), normalize_embeddings=True))

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
        # C: pass both contexts so same-scene contradictions supersede while
        # different-scene coexisting preferences are kept.
        for c in candidates:
            if c.get("_score", 0) >= 0.90:
                continue  # already handled by dedup
            if _check_conflict(
                llm,
                c["content"], item,
                old_context=_parse_context(c.get("metadata_json")),
                new_context=context,
            ):
                conflict_ids.append(c["id"])

        # ── Segment 4: Double-check + save (locked — fast DB write) ─
        # metadata is omitted entirely when context is empty, so callers
        # relying on the historical save() kwargs are unaffected.
        save_kwargs: dict[str, Any] = {
            "session_id": session_id,
            "content": item,
            "category": category,
            "importance": importance,
            "embedding": vec,
        }
        if context:
            save_kwargs["metadata"] = {"context": context}
        with _extraction_lock:
            # Double-check: another thread may have written a duplicate
            if vec:
                recheck = long_term.search(query_embedding=vec, limit=1, min_similarity=0.90)
                if recheck:
                    metrics.incr("writes_skipped_dedup")
                    continue

            new_id = long_term.save(**save_kwargs)
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
