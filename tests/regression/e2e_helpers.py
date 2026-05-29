"""Shared helpers for e2e regression tests.

All assertions are fuzzy to accommodate LLM non-determinism.
"""

from __future__ import annotations

import json
from typing import Any


def assert_answer_contains_keywords(answer: str, keywords: list[str], min_hits: int = 1) -> None:
    """Assert answer contains at least min_hits of the given keywords (case-insensitive)."""
    answer_lower = answer.lower()
    hits = sum(1 for kw in keywords if kw.lower() in answer_lower)
    assert hits >= min_hits, (
        f"Expected at least {min_hits} keyword(s) from {keywords} in answer, got {hits}. "
        f"Answer (first 300 chars): {answer[:300]}"
    )


def assert_answer_length(answer: str, min_chars: int = 10, max_chars: int = 5000) -> None:
    """Assert answer is within reasonable length bounds."""
    length = len(answer.strip())
    assert min_chars <= length <= max_chars, (
        f"Answer length {length} outside [{min_chars}, {max_chars}]. "
        f"Answer (first 200 chars): {answer[:200]}"
    )


def assert_valid_json(text: str) -> dict[str, Any]:
    """Assert text is valid JSON and return parsed result."""
    if "```json" in text:
        start = text.index("```json") + 7
        end = text.index("```", start)
        text = text[start:end].strip()
    elif "```" in text:
        start = text.index("```") + 3
        end = text.index("```", start)
        text = text[start:end].strip()

    parsed = json.loads(text)
    assert isinstance(parsed, dict), f"Expected dict, got {type(parsed).__name__}"
    return parsed


def assert_tool_was_called(result: Any, tool_names: list[str]) -> None:
    """Assert that specific tools were invoked during agent run."""
    called = set()
    if hasattr(result, "tool_calls"):
        for tc in result.tool_calls:
            called.add(tc.get("name", "") if isinstance(tc, dict) else getattr(tc, "name", ""))
    elif hasattr(result, "steps"):
        for step in result.steps:
            if hasattr(step, "tool_calls"):
                for tc in step.tool_calls:
                    called.add(tc.get("name", "") if isinstance(tc, dict) else getattr(tc, "name", ""))

    for name in tool_names:
        assert name in called, f"Tool '{name}' was not called. Called tools: {called}"


def assert_answer_not_empty(answer: str) -> None:
    """Assert answer is not empty or just whitespace."""
    assert answer and answer.strip(), "Answer is empty or whitespace-only"


def assert_no_error_keywords(answer: str) -> None:
    """Assert answer doesn't contain obvious error indicators."""
    error_patterns = ["i cannot", "i'm unable", "error occurred", "failed to", "i don't have access"]
    answer_lower = answer.lower()
    for pat in error_patterns:
        assert pat not in answer_lower, f"Answer contains error pattern '{pat}': {answer[:200]}"
