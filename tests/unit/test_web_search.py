"""Tests for web_search structured tool."""
from unittest.mock import MagicMock, patch
from time import monotonic

from agentnexus.tools.web_search import (
    _pick_depth,
    _make_cache_key,
    _cache_get,
    _cache_set,
    clear_search_cache,
    clear_seen_urls,
    web_search,
    web_search_structured,
    _cache,
)


class TestPickDepth:
    def test_basic_query(self):
        assert _pick_depth("北京天气") == "basic"

    def test_advanced_by_keyword(self):
        assert _pick_depth("对比iPhone和华为") == "advanced"

    def test_advanced_by_year(self):
        assert _pick_depth("2026年经济预测") == "advanced"

    @patch("agentnexus.tools.web_search.datetime")
    def test_advanced_by_dynamic_year(self, mock_dt):
        mock_dt.now.return_value.year = 2028
        assert _pick_depth("2028年趋势") == "advanced"

    @patch("agentnexus.tools.web_search.datetime")
    def test_basic_when_old_year(self, mock_dt):
        mock_dt.now.return_value.year = 2028
        assert _pick_depth("2025年回顾") == "basic"

    def test_advanced_by_date_iso(self):
        assert _pick_depth("2026-06-01发生了什么") == "advanced"

    def test_advanced_by_date_cn(self):
        assert _pick_depth("6月1日新闻") == "advanced"

    def test_advanced_by_month(self):
        assert _pick_depth("5月销量报告") == "advanced"

    def test_advanced_by_today(self):
        assert _pick_depth("今天天气怎么样") == "advanced"

    def test_advanced_by_yesterday(self):
        assert _pick_depth("昨天的比赛结果") == "advanced"

    def test_advanced_by_this_week(self):
        assert _pick_depth("本周热点事件") == "advanced"

    def test_advanced_by_this_month(self):
        assert _pick_depth("本月股市行情") == "advanced"

    def test_advanced_by_this_year(self):
        assert _pick_depth("今年GDP增长") == "advanced"

    def test_advanced_by_english_today(self):
        assert _pick_depth("today's news") == "advanced"

    def test_advanced_by_english_this_week(self):
        assert _pick_depth("this week highlights") == "advanced"

    def test_basic_no_time_hint(self):
        assert _pick_depth("Python教程") == "basic"


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

    @patch("agentnexus.tools.web_search._get_client")
    def test_search_with_include_domains(self, mock_get_client):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.search.return_value = {"results": []}
        web_search_structured("test", include_domains=["arxiv.org"])
        call_kwargs = mock_client.search.call_args[1]
        assert call_kwargs["include_domains"] == ["arxiv.org"]

    @patch("agentnexus.tools.web_search._get_client")
    def test_search_with_exclude_domains(self, mock_get_client):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.search.return_value = {"results": []}
        web_search_structured("test", exclude_domains=["pinterest.com"])
        call_kwargs = mock_client.search.call_args[1]
        assert call_kwargs["exclude_domains"] == ["pinterest.com"]


class TestWebSearch:
    @patch("agentnexus.tools.web_search.web_search_structured")
    def test_no_results_no_client(self, mock_ws):
        mock_ws.return_value = []
        result = web_search("no config test")
        assert "未配置" in result or "未找到" in result


class TestSearchCache:
    def setup_method(self):
        _cache.clear()

    def test_cache_key_hashable(self):
        key = _make_cache_key("test", 5, "basic", None, "general", False, None, None)
        assert isinstance(key, tuple)
        d = {key: "value"}  # should not raise

    def test_cache_set_and_get(self):
        key = _make_cache_key("test", 5, "basic", None, "general", False, None, None)
        results = [{"title": "T", "url": "https://t.com", "content": "c", "score": 0.9}]
        _cache_set(key, results)
        assert _cache_get(key) == results

    def test_cache_miss_returns_none(self):
        key = _make_cache_key("nonexistent", 5, "basic", None, "general", False, None, None)
        assert _cache_get(key) is None

    def test_cache_expiry(self):
        key = _make_cache_key("expire", 5, "basic", None, "general", False, None, None)
        results = [{"title": "T"}]
        # Store with old timestamp
        _cache[key] = (monotonic() - 600, results)
        assert _cache_get(key) is None
        assert key not in _cache  # evicted

    def test_cache_max_size_eviction(self):
        # Fill cache beyond 256 limit
        for i in range(260):
            key = _make_cache_key(f"q{i}", 5, "basic", None, "general", False, None, None)
            _cache_set(key, [{"title": f"T{i}"}])
        assert len(_cache) <= 256

    def test_clear_search_cache(self):
        key = _make_cache_key("test", 5, "basic", None, "general", False, None, None)
        _cache_set(key, [{"title": "T"}])
        clear_search_cache()
        assert len(_cache) == 0

    def test_clear_seen_urls_clears_cache(self):
        key = _make_cache_key("test", 5, "basic", None, "general", False, None, None)
        _cache_set(key, [{"title": "T"}])
        clear_seen_urls()
        assert len(_cache) == 0

    @patch("agentnexus.tools.web_search._get_client")
    def test_cache_hit_skips_api_call(self, mock_get_client):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.search.return_value = {
            "results": [{"title": "Cached", "url": "https://cached.com",
                         "content": "c", "score": 0.9}]
        }
        # First call hits API
        r1 = web_search_structured("cached query")
        assert mock_client.search.call_count == 1
        # Second call should use cache
        r2 = web_search_structured("cached query")
        assert mock_client.search.call_count == 1  # no additional API call
        assert r1 == r2

    @patch("agentnexus.tools.web_search._get_client")
    def test_different_params_miss_cache(self, mock_get_client):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.search.return_value = {"results": []}
        web_search_structured("query A")
        web_search_structured("query B")
        assert mock_client.search.call_count == 2
