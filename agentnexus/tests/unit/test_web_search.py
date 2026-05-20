"""Tests for web_search structured tool."""
from unittest.mock import patch, MagicMock
from agentnexus.tools.web_search import (
    web_search, web_search_structured, _pick_depth,
)


class TestPickDepth:
    def test_basic_query(self):
        assert _pick_depth("北京天气") == "basic"

    def test_advanced_by_keyword(self):
        assert _pick_depth("对比iPhone和华为") == "advanced"

    def test_advanced_by_year(self):
        assert _pick_depth("2026年经济预测") == "advanced"


class TestWebSearchStructured:
    @patch("agentnexus.tools.web_search._get_client")
    def test_basic_search(self, mock_get_client):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.search.return_value = {
            "results": [
                {"title": "Test", "url": "https://test.com",
                 "content": "test content", "score": 0.9}
            ]
        }
        results = web_search_structured("test query")
        assert len(results) == 1
        assert results[0]["title"] == "Test"
        call_args = mock_client.search.call_args
        assert call_args[0][0] == "test query"
        assert call_args[1]["max_results"] == 5

    @patch("agentnexus.tools.web_search._get_client")
    def test_search_with_time_range(self, mock_get_client):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.search.return_value = {"results": []}
        web_search_structured("news", time_range="week")
        call_kwargs = mock_client.search.call_args[1]
        assert call_kwargs["time_range"] == "week"

    @patch("agentnexus.tools.web_search._get_client")
    def test_search_with_topic(self, mock_get_client):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.search.return_value = {"results": []}
        web_search_structured("news", topic="news")
        call_kwargs = mock_client.search.call_args[1]
        assert call_kwargs["topic"] == "news"

    @patch("agentnexus.tools.web_search._get_client")
    def test_search_with_include_answer(self, mock_get_client):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.search.return_value = {
            "answer": "这是直接答案",
            "results": [{"title": "T", "url": "https://t.com",
                         "content": "c", "score": 0.8}]
        }
        web_search_structured("test", include_answer=True)
        call_kwargs = mock_client.search.call_args[1]
        assert call_kwargs["include_answer"] is True


class TestWebSearch:
    @patch("agentnexus.tools.web_search.web_search_structured")
    def test_no_results_no_client(self, mock_ws):
        mock_ws.return_value = []
        result = web_search("no config test")
        assert "未配置" in result or "未找到" in result
