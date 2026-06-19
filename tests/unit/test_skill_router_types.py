"""Tests for agentnexus.skills.router.types — router data structures and constants."""

import re
from unittest.mock import patch

from agentnexus.skills.router.types import (
    _ABBREVIATION_MAP,
    _COMPOSITE_ACTION_OVERRIDES,
    _CONNECTOR_PATTERNS,
    _JSON_OBJECT_RE,
    _OBJECT_LEXICON,
    _PRODUCT_ALIASES,
    _STOPWORDS,
    _TOKEN_RE,
    _VERB_FORMS,
    _VERB_LEXICON,
    IntentSignals,
    IndexedSkillMetadata,
    SkillRoute,
    SkillRouteDecision,
    SkillRouterIndex,
)


class TestTokenRegex:
    def test_matches_english_words(self):
        assert _TOKEN_RE.search("hello world") is not None

    def test_matches_chinese_characters(self):
        match = _TOKEN_RE.search("你好世界")
        assert match is not None

    def test_matches_japanese_katakana(self):
        match = _TOKEN_RE.search("テスト")
        assert match is not None

    def test_matches_korean(self):
        match = _TOKEN_RE.search("테스트")
        assert match is not None

    def test_does_not_match_punctuation_only(self):
        assert _TOKEN_RE.search("!@#$%") is None

    def test_matches_mixed_script(self):
        # \w in Python 3 matches Unicode word chars including CJK,
        # so contiguous mixed-script text is one token
        matches = _TOKEN_RE.findall("Python 编程 test123")
        assert len(matches) >= 2


class TestJsonObjectRegex:
    def test_matches_simple_json(self):
        match = _JSON_OBJECT_RE.search('{"key": "value"}')
        assert match is not None

    def test_matches_nested_json(self):
        match = _JSON_OBJECT_RE.search('{"a": {"b": 1}}')
        assert match is not None

    def test_no_match_without_braces(self):
        assert _JSON_OBJECT_RE.search("no json here") is None


class TestStopwords:
    def test_contains_english_stopwords(self):
        assert "the" in _STOPWORDS
        assert "a" in _STOPWORDS
        assert "is" in _STOPWORDS

    def test_contains_chinese_stopwords(self):
        assert "需要" in _STOPWORDS
        assert "使用" in _STOPWORDS
        assert "帮我" in _STOPWORDS


class TestVerbLexicon:
    def test_create_has_synonyms(self):
        assert "create" in _VERB_LEXICON["创建"]
        assert "新建" in _VERB_LEXICON["创建"]

    def test_search_has_multilingual_synonyms(self):
        synonyms = _VERB_LEXICON["搜索"]
        assert "search" in synonyms
        assert "查找" in synonyms
        assert "lookup" in synonyms

    def test_verb_forms_contains_all_verbs(self):
        assert "创建" in _VERB_FORMS
        assert "create" in _VERB_FORMS
        assert "搜索" in _VERB_FORMS


class TestObjectLexicon:
    def test_document_has_translations(self):
        assert "document" in _OBJECT_LEXICON["文档"]
        assert "文件" in _OBJECT_LEXICON["文档"]

    def test_code_has_language_aliases(self):
        aliases = _OBJECT_LEXICON["代码"]
        assert "python" in aliases
        assert "javascript" in aliases


class TestAbbreviationMap:
    def test_common_abbreviations(self):
        assert _ABBREVIATION_MAP["doc"] == "document"
        assert _ABBREVIATION_MAP["py"] == "python"
        assert _ABBREVIATION_MAP["js"] == "javascript"
        assert _ABBREVIATION_MAP["db"] == "database"


class TestProductAliases:
    def test_word_maps_to_docx(self):
        assert "docx" in _PRODUCT_ALIASES["word"]

    def test_excel_maps_to_xlsx(self):
        assert "xlsx" in _PRODUCT_ALIASES["excel"]


class TestConnectorPatterns:
    def test_sequential_pattern_count(self):
        sequential = [p for p in _CONNECTOR_PATTERNS if p[2] == "sequential"]
        assert len(sequential) >= 3

    def test_conditional_pattern_count(self):
        conditional = [p for p in _CONNECTOR_PATTERNS if p[2] == "conditional"]
        assert len(conditional) >= 2

    def test_sequential_patterns_compile(self):
        for pattern, _, _ in _CONNECTOR_PATTERNS:
            re.compile(pattern)


class TestCompositeActionOverrides:
    def test_search_summarize_prefers_search(self):
        assert _COMPOSITE_ACTION_OVERRIDES[("搜索", "总结")] == "搜索"

    def test_read_edit_prefers_edit(self):
        assert _COMPOSITE_ACTION_OVERRIDES[("读取", "编辑")] == "编辑"


class TestSkillRoute:
    def test_frozen_dataclass(self):
        from pathlib import Path
        from agentnexus.skills.workflow import Workflow

        workflow = Workflow.model_validate({
            "id": "test", "version": "1", "display_name": "Test",
            "description": "A test skill",
            "prompt_profile": {"system": "react"},
            "tool_policy": {"max_risk": "low"},
            "steps": [{"type": "prompt", "id": "s1", "prompt": "Do."}],
            "success_criteria": ["Done."],
        })
        from agentnexus.skills.registry import SkillEntry
        entry = SkillEntry(
            namespace="ns", workflow_id="wf", display_name="Test",
            description="desc", path=Path("/tmp/t"), workflow=workflow,
        )
        route = SkillRoute(entry=entry, score=1.5, matched_terms=("test",), reason="match")
        assert route.score == 1.5
        assert route.source == "deterministic"
        # Frozen — cannot modify
        try:
            route.score = 2.0
            assert False, "Should have raised"
        except AttributeError:
            pass


class TestSkillRouteDecision:
    def test_default_values(self):
        decision = SkillRouteDecision(route=None, candidates=(), uncertain=False, reason="none")
        assert decision.mode == "single"
        assert decision.confidence == 0.0
        assert decision.secondary_skills == ()


class TestIntentSignals:
    def test_default_values(self):
        signals = IntentSignals()
        assert signals.action_verbs == ()
        assert signals.object_nouns == ()
        assert signals.primary_action is None
        assert signals.priority_mode == "default"


class TestIndexedSkillMetadata:
    def test_frozen_with_defaults(self):
        from pathlib import Path
        from agentnexus.skills.workflow import Workflow
        from agentnexus.skills.registry import SkillEntry

        workflow = Workflow.model_validate({
            "id": "test", "version": "1", "display_name": "Test",
            "description": "A test skill",
            "prompt_profile": {"system": "react"},
            "tool_policy": {"max_risk": "low"},
            "steps": [{"type": "prompt", "id": "s1", "prompt": "Do."}],
            "success_criteria": ["Done."],
        })
        entry = SkillEntry(
            namespace="ns", workflow_id="wf", display_name="Test",
            description="desc", path=Path("/tmp/t"), workflow=workflow,
        )
        meta = IndexedSkillMetadata(entry=entry, terms=frozenset({"test"}), id_terms=frozenset(), name_terms=frozenset())
        assert meta.verb_terms == frozenset()
        assert meta.embedding == ()


class TestSkillRouterIndex:
    def test_build_delegates_to_retrieve(self):
        with patch("agentnexus.skills.router.retrieve.build_index") as mock_build:
            mock_build.return_value = SkillRouterIndex(items=(), idf={}, signature=())
            result = SkillRouterIndex.build([], compute_embeddings=False)
            mock_build.assert_called_once_with([], compute_embeddings=False)
            assert isinstance(result, SkillRouterIndex)
