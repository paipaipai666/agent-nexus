"""Long-term memory extraction service."""

from __future__ import annotations

import json
import re
from typing import Any

from agentnexus.prompts import load_prompt
from agentnexus.rag.embeddings import embedding_to_list

EXTRACT_PROMPT = load_prompt("memory_extract")

MEMORY_CATEGORIES = {
    "user_preference": 0.9,
    "entity_fact": 0.7,
    "conclusion": 0.8,
    "conversation": 0.5,
    "task_progress": 0.7,
    "error_pattern": 0.8,
    "tool_preference": 0.6,
}

CATEGORY_LABELS = {
    "user_preference": "偏好",
    "entity_fact": "事实",
    "conclusion": "结论",
    "conversation": "历史",
    "task_progress": "进展",
    "error_pattern": "错误模式",
    "tool_preference": "工具偏好",
}


def extract_xml_tag(text: str, tag: str) -> str | None:
    pattern = rf"<{tag}>\s*(.*?)\s*</{tag}>"
    match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
    return match.group(1) if match else None


def parse_memory_payload(response: str) -> dict:
    try:
        return json.loads(response.strip().lstrip("```json").rstrip("```").strip())
    except Exception:
        return {}


def iter_memory_items(data: dict):
    for category, importance in MEMORY_CATEGORIES.items():
        for item in data.get(category, []):
            if isinstance(item, dict):
                item = item.get("content") or item.get("text") or ""
            if not isinstance(item, str) or len(item.strip()) < 5:
                continue
            yield category, importance, item.strip()


def extract_and_save_memories(
    *,
    llm: Any,
    embed_model: Any,
    long_term: Any,
    session_id: str,
    question: str,
    answer: str,
) -> None:
    prompt = EXTRACT_PROMPT.format(question=question, answer=answer)
    response = llm.think([{"role": "user", "content": prompt}], silent=True) or "{}"
    data = parse_memory_payload(response)
    for category, importance, item in iter_memory_items(data):
        vec = embedding_to_list(embed_model.encode(item, normalize_embeddings=True))
        long_term.save(
            session_id=session_id,
            content=item,
            category=category,
            importance=importance,
            embedding=vec,
        )
