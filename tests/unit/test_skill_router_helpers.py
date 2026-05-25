from pathlib import Path

from agentnexus.skills.registry import SkillEntry
from agentnexus.skills.router import (
    IndexedSkillMetadata,
    SkillRouterIndex,
    _entry_terms,
    _entries_signature,
    _format_reason,
    _parse_llm_skill_id,
    _score_entry,
    _score_indexed_entry,
    _split_mixed_script_boundaries,
    _tokenize,
)
from agentnexus.skills.workflow import Workflow


def _make_entry(
    workflow_id="test-skill",
    display_name="Test Skill",
    description="A test skill",
    namespace="test",
):
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
        path=Path("/tmp/test.yaml"),
        workflow=workflow,
        source_kind="skill",
    )


class TestEntryTerms:
    def test_extracts_terms_from_entry(self):
        entry = _make_entry(
            workflow_id="code-review",
            display_name="Code Review",
            description="Review code changes thoroughly",
        )
        terms = _entry_terms(entry)
        assert "code" in terms
        assert "review" in terms
        assert "changes" in terms
        assert "thoroughly" in terms

    def test_deduplicates_terms(self):
        entry = _make_entry(
            workflow_id="test",
            display_name="test",
            description="test description",
        )
        terms = _entry_terms(entry)
        assert terms.count("test") >= 3

    def test_id_components_separated(self):
        entry = _make_entry(
            workflow_id="code-review",
            display_name="Code Review",
            description="Review code",
        )
        terms = _entry_terms(entry)
        assert "code" in terms
        assert "review" in terms


class TestScoreEntry:
    def test_score_based_on_matched_terms(self):
        entry = _make_entry(
            workflow_id="test-skill",
            display_name="Test Skill",
            description="A test skill",
        )
        query = {"test", "skill", "review"}
        matched = ["test", "skill"]
        score = _score_entry(query, entry, matched)
        assert score > 0

    def test_empty_match_returns_zero(self):
        entry = _make_entry()
        score = _score_entry({"unmatched"}, entry, [])
        assert score == 0.0

    def test_id_terms_get_bonus(self):
        entry = _make_entry(
            workflow_id="code-review",
            display_name="Check Code",
            description="Review code changes",
        )
        query = {"code"}
        matched = ["code"]
        score = _score_entry(query, entry, matched)
        id_terms = frozenset(_tokenize("code review"))
        name_terms = frozenset(_tokenize("Check Code"))
        expected = (
            1.0 * len(matched)
            + 1.5 * len(query & id_terms)
            + 1.0 * len(query & name_terms)
        )
        assert score == expected

    def test_name_terms_get_bonus(self):
        entry = _make_entry(
            workflow_id="unrelated",
            display_name="Find Code",
            description="Locates code snippets",
        )
        query = {"code"}
        matched = ["code"]
        score = _score_entry(query, entry, matched)
        id_terms = frozenset(_tokenize("unrelated"))
        name_terms = frozenset(_tokenize("Find Code"))
        expected = (
            1.0 * len(matched)
            + 1.5 * len(query & id_terms)
            + 1.0 * len(query & name_terms)
        )
        assert score == expected


class TestScoreIndexedEntry:
    def test_score_with_idf_weights(self):
        entry = _make_entry(workflow_id="skill-a", display_name="Alpha", description="Alpha skill")
        other = _make_entry(workflow_id="skill-b", display_name="Beta", description="Beta skill")
        index = SkillRouterIndex.build([entry, other])
        item = index.items[0]
        query = {"alpha", "skill"}
        matched = sorted(query & item.terms)
        score = _score_indexed_entry(query, item, matched, index.idf)
        assert score > 0

    def test_no_match_returns_zero(self):
        entry = _make_entry(workflow_id="alpha", display_name="Alpha", description="Alpha skill")
        item = IndexedSkillMetadata(
            entry=entry,
            terms=frozenset(_entry_terms(entry)),
            id_terms=frozenset(),
            name_terms=frozenset(),
        )
        assert _score_indexed_entry({"unmatched"}, item, [], {}) == 0.0

    def test_workflow_id_exact_match_bonus(self):
        entry = _make_entry(
            workflow_id="draft-writer",
            display_name="Writer",
            description="Writes drafts",
        )
        item = IndexedSkillMetadata(
            entry=entry,
            terms=frozenset(_entry_terms(entry)),
            id_terms=frozenset(_tokenize("draft writer")),
            name_terms=frozenset(_tokenize("Writer")),
        )
        query = {"draft", "writer"}
        matched = sorted(query & item.terms)
        score_no_bonus = _score_indexed_entry(query, item, matched, {})
        score_with_bonus = _score_indexed_entry(
            {"draft", "writer", "draft-writer"}, item,
            sorted({"draft", "writer", "draft-writer"} & item.terms),
            {},
        )
        assert score_with_bonus > score_no_bonus

    def test_combined_scoring(self):
        entry = _make_entry(
            workflow_id="code-review",
            display_name="Code Review",
            description="Review code changes",
        )
        item = IndexedSkillMetadata(
            entry=entry,
            terms=frozenset(_entry_terms(entry)),
            id_terms=frozenset(_tokenize("code review")),
            name_terms=frozenset(_tokenize("Code Review")),
        )
        query = {"code", "review", "changes"}
        matched = sorted(query & item.terms)
        score = _score_indexed_entry(query, item, matched, {})
        base = len(matched) * 1.0
        id_bonus = 1.5 * len(query & item.id_terms)
        name_bonus = 1.0 * len(query & item.name_terms)
        assert score == base + id_bonus + name_bonus


class TestEntriesSignature:
    def test_consistent_signature(self):
        e1 = _make_entry(workflow_id="a", display_name="A", description="First")
        e2 = _make_entry(workflow_id="b", display_name="B", description="Second")
        sig1 = _entries_signature([e1, e2])
        sig2 = _entries_signature([e1, e2])
        assert sig1 == sig2

    def test_different_order_differs(self):
        e1 = _make_entry(workflow_id="a", display_name="A", description="First")
        e2 = _make_entry(workflow_id="b", display_name="B", description="Second")
        sig_ab = _entries_signature([e1, e2])
        sig_ba = _entries_signature([e2, e1])
        assert sig_ab != sig_ba

    def test_includes_all_fields(self):
        e1 = _make_entry(workflow_id="my-id", display_name="My Name", description="My desc")
        sig = _entries_signature([e1])
        assert len(sig) == 1
        assert "test/my-id" in sig[0]
        assert "My Name" in sig[0]
        assert "My desc" in sig[0]


class TestFormatReason:
    def test_formats_reason_with_terms_and_score(self):
        entry = _make_entry(workflow_id="test-skill")
        reason = _format_reason(entry, ["code", "review"], 3.5)
        assert "test/test-skill" in reason
        assert "code" in reason
        assert "review" in reason
        assert "3.5" in reason

    def test_truncated_terms(self):
        entry = _make_entry(workflow_id="test-skill")
        many = [f"t{i}" for i in range(12)]
        reason = _format_reason(entry, many, 5.0)
        assert "t7" in reason
        assert "t8" not in reason

    def test_no_matched_terms(self):
        entry = _make_entry(workflow_id="test-skill")
        reason = _format_reason(entry, [], 0.0)
        assert "-" in reason


class TestSplitMixedScriptBoundaries:
    def test_inserts_space_after_cjk(self):
        assert _split_mixed_script_boundaries("测试code") == "测试 code"

    def test_inserts_space_before_cjk(self):
        assert _split_mixed_script_boundaries("hello世界") == "hello 世界"

    def test_preserves_existing_spaces(self):
        assert _split_mixed_script_boundaries("hello 世界") == "hello 世界"

    def test_pure_cjk_unchanged(self):
        assert _split_mixed_script_boundaries("你好世界") == "你好世界"

    def test_pure_ascii_unchanged(self):
        assert _split_mixed_script_boundaries("hello world") == "hello world"


class TestParseLLMSkillId:
    def test_parses_json_object(self):
        assert _parse_llm_skill_id('{"skill_id": "test"}') == "test"

    def test_parses_json_in_code_block(self):
        assert _parse_llm_skill_id('```json\n{"skill_id": "test"}\n```') == "test"

    def test_parses_json_from_markdown(self):
        assert _parse_llm_skill_id('Selected:\n{"skill_id": "default/draft"}') == "default/draft"

    def test_invalid_json_returns_none(self):
        assert _parse_llm_skill_id("{invalid}") is None

    def test_extra_keys_rejected(self):
        assert _parse_llm_skill_id('{"skill_id": "test", "reason": "x"}') is None

    def test_empty_returns_none(self):
        assert _parse_llm_skill_id("") is None
