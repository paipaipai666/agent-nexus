"""Tests for agentnexus.wiki.verifier — mechanical verification of wiki statements."""

from unittest.mock import MagicMock, patch

from agentnexus.wiki.models import SynthesisLevel, WikiStatement
from agentnexus.wiki.verifier import (
    MechanicalVerifier,
    _tokenize,
    jaccard_similarity,
)


class TestTokenize:
    def test_english_text_splits_on_whitespace(self):
        tokens = _tokenize("hello world")
        assert "hello" in tokens
        assert "world" in tokens

    def test_chinese_characters_become_individual_tokens(self):
        tokens = _tokenize("你好世界")
        assert "你" in tokens
        assert "好" in tokens
        assert "世" in tokens
        assert "界" in tokens

    def test_mixed_chinese_english(self):
        tokens = _tokenize("Python编程语言")
        assert "python" in tokens
        assert "编" in tokens
        assert "程" in tokens

    def test_strips_punctuation(self):
        tokens = _tokenize("hello, world!")
        assert "hello" in tokens
        assert "world" in tokens
        assert "," not in tokens
        assert "!" not in tokens

    def test_normalizes_whitespace(self):
        tokens = _tokenize("hello   world")
        assert len(tokens) == 2

    def test_empty_string_returns_empty_set(self):
        tokens = _tokenize("")
        assert tokens == set()

    def test_lowercases_english(self):
        tokens = _tokenize("Hello WORLD")
        assert "hello" in tokens
        assert "world" in tokens


class TestJaccardSimilarity:
    def test_identical_texts_return_1(self):
        assert jaccard_similarity("hello world", "hello world") == 1.0

    def test_completely_different_texts_return_0(self):
        assert jaccard_similarity("hello", "xyz") == 0.0

    def test_both_empty_returns_1(self):
        assert jaccard_similarity("", "") == 1.0

    def test_one_empty_returns_0(self):
        assert jaccard_similarity("hello", "") == 0.0
        assert jaccard_similarity("", "hello") == 0.0

    def test_partial_overlap(self):
        sim = jaccard_similarity("hello world", "hello there")
        assert 0.0 < sim < 1.0

    def test_symmetric(self):
        a = jaccard_similarity("hello world", "world hello")
        b = jaccard_similarity("world hello", "hello world")
        assert a == b


@patch("agentnexus.wiki.verifier.get_settings")
class TestMechanicalVerifierInit:
    def test_uses_default_thresholds(self, mock_settings):
        mock_settings.return_value = MagicMock(
            wiki_jaccard_direct_quote=0.6,
            wiki_jaccard_paraphrase=0.4,
            wiki_cosine_paraphrase=0.7,
            wiki_cosine_source=0.35,
        )
        v = MechanicalVerifier()
        assert v.jaccard_direct_quote == 0.6
        assert v.jaccard_paraphrase == 0.4

    def test_custom_thresholds_override_defaults(self, mock_settings):
        mock_settings.return_value = MagicMock(
            wiki_jaccard_direct_quote=0.6,
            wiki_jaccard_paraphrase=0.4,
            wiki_cosine_paraphrase=0.7,
            wiki_cosine_source=0.35,
        )
        v = MechanicalVerifier(thresholds={"jaccard_direct_quote": 0.8})
        assert v.jaccard_direct_quote == 0.8
        assert v.jaccard_paraphrase == 0.4  # Unchanged


@patch("agentnexus.wiki.verifier.get_settings")
class TestVerifyStatement:
    def _make_verifier(self, mock_settings):
        mock_settings.return_value = MagicMock(
            wiki_jaccard_direct_quote=0.6,
            wiki_jaccard_paraphrase=0.4,
            wiki_cosine_paraphrase=0.7,
            wiki_cosine_source=0.35,
        )
        return MechanicalVerifier()

    def test_no_source_chunks_returns_synthesis(self, mock_settings):
        v = self._make_verifier(mock_settings)
        stmt = WikiStatement(
            statement_id="s1", page_id="p1", text="test",
            synthesis_level=SynthesisLevel.DIRECT_QUOTE.value,
            source_chunk_ids=[],
        )
        assert v.verify_statement(stmt, {}) == SynthesisLevel.SYNTHESIS.value

    def test_missing_chunk_text_returns_synthesis(self, mock_settings):
        v = self._make_verifier(mock_settings)
        stmt = WikiStatement(
            statement_id="s1", page_id="p1", text="test",
            synthesis_level=SynthesisLevel.DIRECT_QUOTE.value,
            source_chunk_ids=["c1"],
        )
        assert v.verify_statement(stmt, {}) == SynthesisLevel.SYNTHESIS.value

    def test_synthesis_stays_synthesis(self, mock_settings):
        v = self._make_verifier(mock_settings)
        stmt = WikiStatement(
            statement_id="s1", page_id="p1", text="test",
            synthesis_level=SynthesisLevel.SYNTHESIS.value,
            source_chunk_ids=["c1"],
        )
        assert v.verify_statement(stmt, {"c1": "chunk text"}) == SynthesisLevel.SYNTHESIS.value

    @patch("agentnexus.wiki.verifier.cosine_similarity")
    def test_high_jaccard_returns_direct_quote(self, mock_cosine, mock_settings):
        v = self._make_verifier(mock_settings)
        # Use identical texts for high Jaccard
        text = "the quick brown fox jumps"
        stmt = WikiStatement(
            statement_id="s1", page_id="p1", text=text,
            synthesis_level=SynthesisLevel.DIRECT_QUOTE.value,
            source_chunk_ids=["c1"],
        )
        result = v.verify_statement(stmt, {"c1": text})
        assert result == SynthesisLevel.DIRECT_QUOTE.value

    @patch("agentnexus.wiki.verifier.cosine_similarity")
    def test_cross_reference_with_valid_multi_source(self, mock_cosine, mock_settings):
        mock_cosine.return_value = 0.5  # Above cosine_source threshold
        v = self._make_verifier(mock_settings)
        stmt = WikiStatement(
            statement_id="s1", page_id="p1", text="combined insight",
            synthesis_level=SynthesisLevel.CROSS_REFERENCE.value,
            source_chunk_ids=["c1", "c2"],
        )
        result = v.verify_statement(stmt, {"c1": "text1", "c2": "text2"})
        assert result == SynthesisLevel.CROSS_REFERENCE.value

    @patch("agentnexus.wiki.verifier.cosine_similarity")
    def test_cross_reference_with_no_valid_sources_returns_synthesis(self, mock_cosine, mock_settings):
        mock_cosine.return_value = 0.1  # Below cosine_source threshold
        v = self._make_verifier(mock_settings)
        stmt = WikiStatement(
            statement_id="s1", page_id="p1", text="unrelated",
            synthesis_level=SynthesisLevel.CROSS_REFERENCE.value,
            source_chunk_ids=["c1", "c2"],
        )
        result = v.verify_statement(stmt, {"c1": "text1", "c2": "text2"})
        assert result == SynthesisLevel.SYNTHESIS.value


@patch("agentnexus.wiki.verifier.get_settings")
class TestVerifyAndUpdateStatement:
    def test_returns_changed_when_level_differs(self, mock_settings):
        mock_settings.return_value = MagicMock(
            wiki_jaccard_direct_quote=0.6,
            wiki_jaccard_paraphrase=0.4,
            wiki_cosine_paraphrase=0.7,
            wiki_cosine_source=0.35,
        )
        v = MechanicalVerifier()
        stmt = WikiStatement(
            statement_id="s1", page_id="p1", text="test",
            synthesis_level=SynthesisLevel.DIRECT_QUOTE.value,
            source_chunk_ids=[],
        )
        new_level, changed = v.verify_and_update_statement(stmt, {})
        assert new_level == SynthesisLevel.SYNTHESIS.value
        assert changed is True

    def test_returns_unchanged_when_level_same(self, mock_settings):
        mock_settings.return_value = MagicMock(
            wiki_jaccard_direct_quote=0.6,
            wiki_jaccard_paraphrase=0.4,
            wiki_cosine_paraphrase=0.7,
            wiki_cosine_source=0.35,
        )
        v = MechanicalVerifier()
        stmt = WikiStatement(
            statement_id="s1", page_id="p1", text="test",
            synthesis_level=SynthesisLevel.SYNTHESIS.value,
            source_chunk_ids=["c1"],
        )
        new_level, changed = v.verify_and_update_statement(stmt, {"c1": "chunk"})
        assert new_level == SynthesisLevel.SYNTHESIS.value
        assert changed is False
