from types import SimpleNamespace
from unittest.mock import MagicMock

from agentnexus.rag.query_expansion import (
    dedupe_preserve_order,
    expand_queries,
    generate_hypothetical_document,
    looks_like_question,
    rewrite_query,
)


def _make_settings(**overrides):
    defaults = dict(
        enable_query_rewrite=True,
        enable_multi_query=True,
        rag_multi_query_count=3,
        enable_hyde=False,
        hyde_question_only=True,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class TestDedupePreserveOrder:
    def test_empty_list(self):
        assert dedupe_preserve_order([]) == []

    def test_no_duplicates(self):
        assert dedupe_preserve_order(["a", "b", "c"]) == ["a", "b", "c"]

    def test_case_insensitive_dedup(self):
        assert dedupe_preserve_order(["Hello", "hello", "HELLO"]) == ["Hello"]

    def test_whitespace_dedup(self):
        assert dedupe_preserve_order(["  foo  ", "foo", " bar ", "bar"]) == ["foo", "bar"]

    def test_empty_strings_skipped(self):
        assert dedupe_preserve_order(["", "  ", "a", "", "b"]) == ["a", "b"]

    def test_preserves_first_occurrence(self):
        assert dedupe_preserve_order(["Alpha", "beta", "alpha", "Beta"]) == ["Alpha", "beta"]


class TestLooksLikeQuestion:
    def test_empty_string(self):
        assert looks_like_question("") is False

    def test_whitespace_only(self):
        assert looks_like_question("   ") is False

    def test_question_mark(self):
        assert looks_like_question("what is this?") is True

    def test_fullwidth_question_mark(self):
        assert looks_like_question("这是什么？") is True

    def test_chinese_token_shenme(self):
        assert looks_like_question("什么是BM25") is True

    def test_chinese_token_ruhe(self):
        assert looks_like_question("如何优化检索") is True

    def test_chinese_token_weishenme(self):
        assert looks_like_question("为什么失败了") is True

    def test_english_token_what(self):
        assert looks_like_question("what is retrieval") is True

    def test_english_token_why(self):
        assert looks_like_question("why is the sky blue") is True

    def test_english_token_how(self):
        assert looks_like_question("how to install") is True

    def test_non_question_statement(self):
        assert looks_like_question("install python on ubuntu") is False

    def test_non_question_chinese(self):
        assert looks_like_question("安装指南") is False


class TestRewriteQuery:
    def test_returns_original_when_disabled(self, monkeypatch):
        monkeypatch.setattr(
            "agentnexus.rag.query_expansion.get_settings",
            lambda: _make_settings(enable_query_rewrite=False),
        )
        assert rewrite_query("原始问题") == "原始问题"

    def test_returns_rewritten_when_llm_succeeds(self, monkeypatch):
        monkeypatch.setattr(
            "agentnexus.rag.query_expansion.get_settings",
            lambda: _make_settings(enable_query_rewrite=True),
        )
        fake_llm = MagicMock()
        fake_llm.think.return_value = "优化后的查询文本"
        assert rewrite_query("原始问题", llm=fake_llm) == "优化后的查询文本"

    def test_returns_original_when_llm_returns_short(self, monkeypatch):
        monkeypatch.setattr(
            "agentnexus.rag.query_expansion.get_settings",
            lambda: _make_settings(enable_query_rewrite=True),
        )
        fake_llm = MagicMock()
        fake_llm.think.return_value = "x"
        assert rewrite_query("原始问题", llm=fake_llm) == "原始问题"

    def test_returns_original_when_llm_raises(self, monkeypatch):
        monkeypatch.setattr(
            "agentnexus.rag.query_expansion.get_settings",
            lambda: _make_settings(enable_query_rewrite=True),
        )
        fake_llm = MagicMock()
        fake_llm.think.side_effect = RuntimeError("boom")
        assert rewrite_query("原始问题", llm=fake_llm) == "原始问题"


class TestExpandQueries:
    def test_returns_single_when_multi_query_disabled(self, monkeypatch):
        monkeypatch.setattr(
            "agentnexus.rag.query_expansion.get_settings",
            lambda: _make_settings(enable_query_rewrite=False, enable_multi_query=False),
        )
        result = expand_queries("test query")
        assert result == ["test query"]

    def test_expands_with_mock_llm(self, monkeypatch):
        monkeypatch.setattr(
            "agentnexus.rag.query_expansion.get_settings",
            lambda: _make_settings(
                enable_query_rewrite=True,
                enable_multi_query=True,
                rag_multi_query_count=3,
            ),
        )
        call_count = {"n": 0}

        def fake_think(messages, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return "rewritten query"
            return "- alt question one\n- alt question two"

        fake_llm = MagicMock()
        fake_llm.think.side_effect = fake_think

        result = expand_queries("test query", llm=fake_llm)

        assert result[0] == "test query"
        assert "rewritten query" in result
        assert "alt question one" in result
        assert len(result) == len(set(result))

    def test_deduplicates_across_rewrite_and_expansion(self, monkeypatch):
        monkeypatch.setattr(
            "agentnexus.rag.query_expansion.get_settings",
            lambda: _make_settings(
                enable_query_rewrite=True,
                enable_multi_query=True,
                rag_multi_query_count=3,
            ),
        )
        call_count = {"n": 0}

        def fake_think(messages, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return "test query"
            return "test query\nanother way"

        fake_llm = MagicMock()
        fake_llm.think.side_effect = fake_think

        result = expand_queries("test query", llm=fake_llm)

        assert result.count("test query") == 1
        assert "another way" in result


class TestGenerateHypotheticalDocument:
    def test_returns_empty_when_hyde_disabled(self, monkeypatch):
        monkeypatch.setattr(
            "agentnexus.rag.query_expansion.get_settings",
            lambda: _make_settings(enable_hyde=False),
        )
        assert generate_hypothetical_document("what is BM25") == ""

    def test_returns_empty_when_question_only_and_not_question(self, monkeypatch):
        monkeypatch.setattr(
            "agentnexus.rag.query_expansion.get_settings",
            lambda: _make_settings(enable_hyde=True, hyde_question_only=True),
        )
        assert generate_hypothetical_document("install python") == ""

    def test_returns_doc_when_question_only_and_is_question(self, monkeypatch):
        monkeypatch.setattr(
            "agentnexus.rag.query_expansion.get_settings",
            lambda: _make_settings(enable_hyde=True, hyde_question_only=True),
        )
        fake_llm = MagicMock()
        fake_llm.think.return_value = "A hypothetical answer about BM25."
        result = generate_hypothetical_document("what is BM25?", llm=fake_llm)
        assert result == "A hypothetical answer about BM25."

    def test_returns_doc_when_hyde_enabled_and_not_question_only(self, monkeypatch):
        monkeypatch.setattr(
            "agentnexus.rag.query_expansion.get_settings",
            lambda: _make_settings(enable_hyde=True, hyde_question_only=False),
        )
        fake_llm = MagicMock()
        fake_llm.think.return_value = "Hypothetical doc for plain statement."
        result = generate_hypothetical_document("install python", llm=fake_llm)
        assert result == "Hypothetical doc for plain statement."

    def test_returns_empty_when_llm_raises(self, monkeypatch):
        monkeypatch.setattr(
            "agentnexus.rag.query_expansion.get_settings",
            lambda: _make_settings(enable_hyde=True, hyde_question_only=False),
        )
        fake_llm = MagicMock()
        fake_llm.think.side_effect = RuntimeError("boom")
        assert generate_hypothetical_document("test query", llm=fake_llm) == ""
