"""Tests for the LLM-based routing fallback (disambiguation)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agentnexus.skills.registry import SkillEntry
from agentnexus.skills.router.llm_fallback import parse_llm_skill_id, route_with_llm
from agentnexus.skills.router.types import SkillRoute
from agentnexus.skills.workflow import Workflow


# ── Helpers ─────────────────────────────────────────────────────────


def _make_entry(
    workflow_id: str = "test-skill",
    display_name: str = "Test Skill",
    description: str = "A test skill",
    namespace: str = "test",
) -> SkillEntry:
    workflow = Workflow.model_validate({
        "id": workflow_id,
        "version": "1",
        "display_name": display_name,
        "description": description,
        "prompt_profile": {"system": "react"},
        "tool_policy": {"max_risk": "low"},
        "steps": [{"type": "prompt", "id": "inspect", "prompt": "Inspect."}],
        "success_criteria": ["Done."],
    })
    return SkillEntry(
        namespace=namespace,
        workflow_id=workflow_id,
        display_name=display_name,
        description=description,
        path=Path(f"/tmp/{workflow_id}.yaml"),
        workflow=workflow,
        source_kind="skill",
    )


def _make_route(
    workflow_id: str = "test-skill",
    display_name: str = "Test Skill",
    description: str = "A test skill",
    namespace: str = "test",
    score: float = 5.0,
    matched_terms: tuple[str, ...] = ("test",),
    reason: str = "matched",
    source: str = "deterministic",
) -> SkillRoute:
    return SkillRoute(
        entry=_make_entry(workflow_id, display_name, description, namespace),
        score=score,
        matched_terms=matched_terms,
        reason=reason,
        source=source,
    )


# ── Tests for route_with_llm ───────────────────────────────────────


class TestRouteWithLLM:
    def test_empty_candidates_returns_none(self, mocker):
        llm = MagicMock()
        result = route_with_llm("hello", (), "no match", llm)
        assert result is None

    def test_valid_response_with_known_skill_id(self, mocker):
        route = _make_route("code-review", "Code Review", "Review code")
        llm = MagicMock()
        llm.think.return_value = '{"skill_id": "test/code-review", "confidence": 0.9, "reason": "fits"}'
        result = route_with_llm("review code", (route,), "uncertain", llm)
        assert result is not None
        assert result.source == "llm"
        assert result.entry.qualified_id == "test/code-review"

    def test_valid_response_with_unknown_skill_id(self, mocker):
        route = _make_route("code-review", "Code Review", "Review code")
        llm = MagicMock()
        llm.think.return_value = '{"skill_id": "test/nonexistent", "confidence": 0.9, "reason": "guessing"}'
        result = route_with_llm("review code", (route,), "uncertain", llm)
        assert result is None

    def test_llm_raises_exception_returns_none(self, mocker):
        route = _make_route()
        llm = MagicMock()
        llm.think.side_effect = RuntimeError("LLM unavailable")
        result = route_with_llm("hello", (route,), "uncertain", llm)
        assert result is None

    def test_llm_raises_typeerror_fallback_path(self, mocker):
        route = _make_route("code-review", "Code Review", "Review code")
        llm = MagicMock()
        llm.think.side_effect = [
            TypeError("unsupported parameter"),
            '{"skill_id": "test/code-review", "confidence": 0.8, "reason": "fallback"}',
        ]
        result = route_with_llm("review code", (route,), "uncertain", llm)
        assert result is not None
        assert result.source == "llm"
        assert llm.think.call_count == 2

    def test_skill_id_null_returns_none(self, mocker):
        route = _make_route()
        llm = MagicMock()
        llm.think.return_value = '{"skill_id": null, "confidence": 0.1, "reason": "unclear"}'
        result = route_with_llm("hello", (route,), "uncertain", llm)
        assert result is None

    def test_skill_id_is_integer_returns_none(self, mocker):
        route = _make_route()
        llm = MagicMock()
        llm.think.return_value = '{"skill_id": 123, "confidence": 0.5, "reason": "numeric"}'
        result = route_with_llm("hello", (route,), "uncertain", llm)
        assert result is None


# ── Tests for parse_llm_skill_id ───────────────────────────────────


class TestParseLLMSkillId:
    def test_empty_string_returns_none(self):
        assert parse_llm_skill_id("") is None

    def test_blank_whitespace_returns_none(self):
        assert parse_llm_skill_id("   \n\t  ") is None

    def test_valid_json_with_skill_id(self):
        raw = '{"skill_id": "test/code-review", "confidence": 0.9}'
        assert parse_llm_skill_id(raw) == "test/code-review"

    def test_json_in_code_block(self):
        raw = '```json\n{"skill_id": "default/draft"}\n```'
        assert parse_llm_skill_id(raw) == "default/draft"

    def test_json_embedded_in_text(self):
        raw = 'I recommend: {"skill_id": "ns/skill-x"} for this task.'
        assert parse_llm_skill_id(raw) == "ns/skill-x"

    def test_no_json_returns_none(self):
        raw = "I think we should not use any skill."
        assert parse_llm_skill_id(raw) is None

    def test_invalid_json_returns_none(self):
        raw = '{"skill_id": broken json}'
        assert parse_llm_skill_id(raw) is None

    def test_json_array_returns_none(self):
        raw = '["skill_id", "test"]'
        assert parse_llm_skill_id(raw) is None

    def test_skill_id_null_returns_none(self):
        raw = '{"skill_id": null}'
        assert parse_llm_skill_id(raw) is None

    def test_skill_id_is_integer_returns_none(self):
        raw = '{"skill_id": 42}'
        assert parse_llm_skill_id(raw) is None

    def test_skill_id_over_200_chars_returns_none(self):
        long_id = "a" * 201
        raw = f'{{"skill_id": "{long_id}"}}'
        assert parse_llm_skill_id(raw) is None

    def test_skill_id_empty_after_strip_returns_none(self):
        raw = '{"skill_id": "   "}'
        assert parse_llm_skill_id(raw) is None

    def test_skill_id_whitespace_stripped(self):
        raw = '{"skill_id": "  test/code-review  "}'
        assert parse_llm_skill_id(raw) == "test/code-review"
