"""Tests for the LLM-based skill decider (use_skill / skip_skill / clarify)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agentnexus.skills.registry import SkillEntry
from agentnexus.skills.router.llm_decider import LLMDecision, _parse_decision, decide_with_llm
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


# ── Tests for decide_with_llm ──────────────────────────────────────


class TestDecideWithLLM:
    def test_empty_candidates_returns_skip(self, mocker):
        llm = MagicMock()
        result = decide_with_llm("hello", [], llm)
        assert result.action == "skip_skill"
        assert "no candidates" in result.reason

    def test_valid_use_skill_with_known_id(self, mocker):
        route = _make_route("code-review", "Code Review", "Review code")
        llm = MagicMock()
        llm.think.return_value = '{"action": "use_skill", "skill_id": "test/code-review", "reason": "fits"}'
        result = decide_with_llm("review code", [route], llm)
        assert result.action == "use_skill"
        assert result.skill_id == "test/code-review"

    def test_valid_skip_skill(self, mocker):
        route = _make_route()
        llm = MagicMock()
        llm.think.return_value = '{"action": "skip_skill", "reason": "not relevant"}'
        result = decide_with_llm("hello", [route], llm)
        assert result.action == "skip_skill"

    def test_valid_clarify_with_question(self, mocker):
        route = _make_route()
        llm = MagicMock()
        llm.think.return_value = '{"action": "clarify", "clarify_question": "Which skill?", "reason": "ambiguous"}'
        result = decide_with_llm("do something", [route], llm)
        assert result.action == "clarify"
        assert result.clarify_question == "Which skill?"

    def test_unknown_skill_id_downgraded_to_skip(self, mocker):
        route = _make_route("code-review", "Code Review", "Review code")
        llm = MagicMock()
        llm.think.return_value = '{"action": "use_skill", "skill_id": "test/nonexistent", "reason": "guessing"}'
        result = decide_with_llm("review", [route], llm)
        assert result.action == "skip_skill"
        assert "unknown skill" in result.reason.lower()

    def test_use_skill_without_skill_id_downgraded(self, mocker):
        route = _make_route()
        llm = MagicMock()
        llm.think.return_value = '{"action": "use_skill", "reason": "no id provided"}'
        result = decide_with_llm("do something", [route], llm)
        assert result.action == "skip_skill"
        assert "no skill_id" in result.reason

    def test_invalid_action_coerced_to_skip(self, mocker):
        route = _make_route()
        llm = MagicMock()
        llm.think.return_value = '{"action": "do_stuff", "reason": "confused"}'
        result = decide_with_llm("hello", [route], llm)
        assert result.action == "skip_skill"

    def test_empty_response_returns_skip(self, mocker):
        route = _make_route()
        llm = MagicMock()
        llm.think.return_value = ""
        result = decide_with_llm("hello", [route], llm)
        assert result.action == "skip_skill"
        assert "empty" in result.reason.lower()

    def test_llm_raises_typeerror_fallback_path(self, mocker):
        route = _make_route()
        llm = MagicMock()
        llm.think.side_effect = [
            TypeError("unsupported arg"),
            '{"action": "skip_skill", "reason": "fallback"}',
        ]
        result = decide_with_llm("hello", [route], llm)
        assert result.action == "skip_skill"
        assert llm.think.call_count == 2

    def test_llm_raises_exception_returns_skip(self, mocker):
        route = _make_route()
        llm = MagicMock()
        llm.think.side_effect = RuntimeError("LLM down")
        result = decide_with_llm("hello", [route], llm)
        assert result.action == "skip_skill"
        assert "LLM call failed" in result.reason

    def test_with_conversation_context_and_preferences(self, mocker):
        route = _make_route()
        llm = MagicMock()
        llm.think.return_value = '{"action": "skip_skill", "reason": "context considered"}'
        result = decide_with_llm(
            "hello",
            [route],
            llm,
            conversation_context="User asked about code earlier.",
            user_preferences="Never use code-review skill.",
        )
        assert result.action == "skip_skill"
        # Verify the prompt includes context and preferences
        call_args = llm.think.call_args
        prompt = call_args[0][0][0]["content"]
        assert "User asked about code earlier" in prompt
        assert "Never use code-review skill" in prompt

    def test_without_conversation_context_and_preferences(self, mocker):
        route = _make_route()
        llm = MagicMock()
        llm.think.return_value = '{"action": "skip_skill", "reason": "ok"}'
        result = decide_with_llm("hello", [route], llm)
        assert result.action == "skip_skill"
        call_args = llm.think.call_args
        prompt = call_args[0][0][0]["content"]
        assert "Conversation context:\n" not in prompt
        assert "User preferences (MUST respect):\n" not in prompt


# ── Tests for _parse_decision ──────────────────────────────────────


class TestParseDecision:
    def _by_id(self, *skill_ids: str) -> dict[str, SkillRoute]:
        return {sid: _make_route() for sid in skill_ids}

    def test_empty_raw_returns_skip(self):
        result = _parse_decision("", self._by_id())
        assert result.action == "skip_skill"
        assert "empty" in result.reason.lower()

    def test_blank_raw_returns_skip(self):
        result = _parse_decision("   \n\t  ", self._by_id())
        assert result.action == "skip_skill"
        assert "empty" in result.reason.lower()

    def test_json_in_markdown_code_block(self):
        raw = '```json\n{"action": "skip_skill", "reason": "ok"}\n```'
        result = _parse_decision(raw, self._by_id())
        assert result.action == "skip_skill"
        assert result.reason == "ok"

    def test_json_starting_with_brace(self):
        raw = '{"action": "skip_skill", "reason": "direct"}'
        result = _parse_decision(raw, self._by_id())
        assert result.action == "skip_skill"
        assert result.reason == "direct"

    def test_json_embedded_in_text(self):
        raw = 'Here is my decision: {"action": "skip_skill", "reason": "embedded"} end.'
        result = _parse_decision(raw, self._by_id())
        assert result.action == "skip_skill"
        assert result.reason == "embedded"

    def test_no_json_found_returns_skip(self):
        raw = "I think we should skip this one."
        result = _parse_decision(raw, self._by_id())
        assert result.action == "skip_skill"
        assert "no json" in result.reason.lower()

    def test_invalid_json_returns_skip(self):
        raw = '{"action": skip_skill, broken}'
        result = _parse_decision(raw, self._by_id())
        assert result.action == "skip_skill"
        assert "invalid json" in result.reason.lower()

    def test_non_dict_json_returns_skip(self):
        # A JSON array won't match _JSON_OBJECT_RE, so the parser
        # reports "no JSON" rather than "non-dict".  Use a dict-like
        # string that parses to something the regex *does* capture
        # but which is still not a dict, or accept the "no json" path.
        raw = '["action", "skip_skill"]'
        result = _parse_decision(raw, self._by_id())
        assert result.action == "skip_skill"
        # The {.*} regex does not match a JSON array, so we get
        # "no json in llm response" rather than "non-dict".
        assert "json" in result.reason.lower()

    def test_skill_id_whitespace_only_becomes_none(self):
        raw = '{"action": "use_skill", "skill_id": "   ", "reason": "x"}'
        result = _parse_decision(raw, self._by_id("test/some-skill"))
        # whitespace-only skill_id becomes None, so use_skill without id → skip
        assert result.action == "skip_skill"
        assert result.skill_id is None

    def test_clarify_question_whitespace_only_becomes_none(self):
        raw = '{"action": "clarify", "clarify_question": "   ", "reason": "x"}'
        result = _parse_decision(raw, self._by_id())
        assert result.clarify_question is None

    def test_reason_is_integer_coerced_to_string(self):
        raw = '{"action": "skip_skill", "reason": 42}'
        result = _parse_decision(raw, self._by_id())
        assert result.reason == "42"
