"""Tests for agentnexus.tools.memory_search."""

from unittest.mock import patch

from agentnexus.tools.memory_search import _rewrite_query, _score_stars, memory_search


class TestScoreStars:
    def test_high(self):
        assert _score_stars(0.7) == "★★★"
        assert _score_stars(0.85) == "★★★"
        assert _score_stars(1.0) == "★★★"

    def test_medium(self):
        assert _score_stars(0.5) == "★★☆"
        assert _score_stars(0.6) == "★★☆"
        assert _score_stars(0.69) == "★★☆"

    def test_low(self):
        assert _score_stars(0.0) == "★☆☆"
        assert _score_stars(0.3) == "★☆☆"
        assert _score_stars(0.49) == "★☆☆"

    def test_edge_cases(self):
        assert _score_stars(0.7) == "★★★"
        assert _score_stars(0.5) == "★★☆"
        assert _score_stars(0.0) == "★☆☆"


class TestRewriteQuery:
    @patch("agentnexus.tools.memory_search.AgentLLM")
    def test_successful_rewrite(self, MockLLM):
        MockLLM.return_value.think.return_value = "keyword1 keyword2"
        result = _rewrite_query("What is the weather?")
        assert result == "keyword1 keyword2"

    @patch("agentnexus.tools.memory_search.AgentLLM")
    def test_rewrite_returns_original_on_failure(self, MockLLM):
        MockLLM.return_value.think.side_effect = Exception("LLM error")
        result = _rewrite_query("test query")
        assert result == "test query"

    @patch("agentnexus.tools.memory_search.AgentLLM")
    def test_rewrite_returns_original_on_short_result(self, MockLLM):
        MockLLM.return_value.think.return_value = "x"
        result = _rewrite_query("test query")
        assert result == "test query"


class TestMemorySearch:
    @patch("agentnexus.tools.memory_search.get_embedding_model")
    @patch("agentnexus.tools.memory_search.get_long_term_memory")
    @patch("agentnexus.tools.memory_search.AgentLLM")
    def test_found_results(self, MockLLM, mock_ltm, mock_embed):
        MockLLM.return_value.think.return_value = "python preference"
        mock_embed.return_value.encode.return_value.tolist.return_value = [0.1]
        mock_ltm.return_value.search.return_value = [
            {"_score": 0.85, "category": "user_preference", "content": "User likes Python"},
            {"_score": 0.55, "category": "entity_fact", "content": "Python version 3.11"},
        ]
        result = memory_search("What does user like?")
        assert "相关记忆" in result
        assert "python preference" in result
        assert "★★★" in result
        assert "★★☆" in result
        assert "User likes Python" in result
        assert "Python version 3.11" in result

    @patch("agentnexus.tools.memory_search.get_embedding_model")
    @patch("agentnexus.tools.memory_search.get_long_term_memory")
    @patch("agentnexus.tools.memory_search.AgentLLM")
    def test_no_results(self, MockLLM, mock_ltm, mock_embed):
        MockLLM.return_value.think.return_value = "keywords"
        mock_embed.return_value.encode.return_value.tolist.return_value = [0.1]
        mock_ltm.return_value.search.return_value = []
        result = memory_search("something")
        assert "未找到相关记忆" in result

    @patch("agentnexus.tools.memory_search.get_embedding_model")
    def test_embedding_unavailable(self, mock_embed):
        mock_embed.return_value.encode.side_effect = Exception("model fail")
        result = memory_search("test")
        assert "嵌入模型不可用" in result

    @patch("agentnexus.tools.memory_search.get_embedding_model")
    @patch("agentnexus.tools.memory_search.get_long_term_memory")
    @patch("agentnexus.tools.memory_search.AgentLLM")
    def test_query_rewrite_used(self, MockLLM, mock_ltm, mock_embed):
        MockLLM.return_value.think.return_value = "rewritten keywords"
        mock_embed.return_value.encode.return_value.tolist.return_value = [0.1]
        mock_ltm.return_value.search.return_value = []
        memory_search("original query")
        mock_embed.return_value.encode.assert_called_with(
            "rewritten keywords", normalize_embeddings=True
        )

    @patch("agentnexus.tools.memory_search.get_embedding_model")
    @patch("agentnexus.tools.memory_search.get_long_term_memory")
    @patch("agentnexus.tools.memory_search.AgentLLM")
    def test_category_filter(self, MockLLM, mock_ltm, mock_embed):
        MockLLM.return_value.think.return_value = "keywords"
        mock_embed.return_value.encode.return_value.tolist.return_value = [0.1]
        memory_search("query", category="user_preference")
        _, kwargs = mock_ltm.return_value.search.call_args
        assert kwargs["category"] == "user_preference"

        memory_search("query")
        _, kwargs = mock_ltm.return_value.search.call_args
        assert kwargs["category"] is None
