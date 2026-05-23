"""Tests for agentnexus.tools.memory_search."""

from unittest.mock import MagicMock, patch

from agentnexus.tools.memory_search import _rewrite_query, _score_stars, memory_search


class TestScoreStars:
    def test_high_score(self):
        assert _score_stars(0.8) == "★★★"

    def test_medium_score(self):
        assert _score_stars(0.6) == "★★☆"

    def test_low_score(self):
        assert _score_stars(0.3) == "★☆☆"


class TestRewriteQuery:
    @patch("agentnexus.tools.memory_search.AgentLLM")
    def test_rewrite_succeeds(self, MockLLM):
        MockLLM.return_value.think.return_value = "keyword1 keyword2"
        result = _rewrite_query("What is the weather?")
        assert result == "keyword1 keyword2"

    @patch("agentnexus.tools.memory_search.AgentLLM")
    def test_rewrite_fails_falls_back(self, MockLLM):
        MockLLM.return_value.think.side_effect = Exception("LLM error")
        result = _rewrite_query("test query")
        assert result == "test query"

    @patch("agentnexus.tools.memory_search.AgentLLM")
    def test_rewrite_too_short_falls_back(self, MockLLM):
        MockLLM.return_value.think.return_value = "x"
        result = _rewrite_query("test query")
        assert result == "test query"


class TestMemorySearch:
    @patch("agentnexus.tools.memory_search.get_embedding_model")
    @patch("agentnexus.tools.memory_search.get_long_term_memory")
    @patch("agentnexus.tools.memory_search.AgentLLM")
    def test_no_results(self, MockLLM, mock_ltm, mock_embed):
        MockLLM.return_value.think.return_value = "keywords"
        mock_embed.return_value.encode.return_value.tolist.return_value = [0.1]
        mock_ltm.return_value.search.return_value = []
        result = memory_search("something")
        assert "未找到" in result

    @patch("agentnexus.tools.memory_search.get_embedding_model")
    @patch("agentnexus.tools.memory_search.get_long_term_memory")
    @patch("agentnexus.tools.memory_search.AgentLLM")
    def test_with_results(self, MockLLM, mock_ltm, mock_embed):
        MockLLM.return_value.think.return_value = "keywords"
        mock_embed.return_value.encode.return_value.tolist.return_value = [0.1]
        mock_ltm.return_value.search.return_value = [
            {"category": "user_preference", "content": "likes Python", "_score": 0.85}
        ]
        result = memory_search("Python preference")
        assert "likes Python" in result
        assert "★★★" in result

    @patch("agentnexus.tools.memory_search.get_embedding_model")
    def test_embedding_error(self, mock_embed):
        mock_embed.return_value.encode.side_effect = Exception("model fail")
        result = memory_search("test")
        assert "不可用" in result
