"""Tests for web_fetch tool."""
from unittest.mock import MagicMock, patch
from time import monotonic

from agentnexus.tools.web_fetch import (
    _make_cache_key,
    _cache_get,
    _cache_set,
    clear_fetch_cache,
    web_fetch,
    web_fetch_structured,
    _cache,
)


class TestCacheHelpers:
    def setup_method(self):
        _cache.clear()

    def test_cache_key_hashable(self):
        key = _make_cache_key(("https://example.com",), "basic", "markdown")
        assert isinstance(key, tuple)
        d = {key: "value"}  # should not raise

    def test_cache_set_and_get(self):
        key = _make_cache_key(("https://example.com",), "basic", "markdown")
        _cache_set(key, "cached content")
        assert _cache_get(key) == "cached content"

    def test_cache_miss_returns_none(self):
        key = _make_cache_key(("https://nonexistent.com",), "basic", "markdown")
        assert _cache_get(key) is None

    def test_cache_expiry(self):
        key = _make_cache_key(("https://expire.com",), "basic", "markdown")
        _cache[key] = (monotonic() - 600, "old content")
        assert _cache_get(key) is None
        assert key not in _cache

    def test_cache_max_size_eviction(self):
        for i in range(132):
            key = _make_cache_key((f"https://site{i}.com",), "basic", "markdown")
            _cache_set(key, f"content {i}")
        assert len(_cache) <= 128

    def test_clear_fetch_cache(self):
        key = _make_cache_key(("https://example.com",), "basic", "markdown")
        _cache_set(key, "content")
        clear_fetch_cache()
        assert len(_cache) == 0


class TestWebFetchStructured:
    @patch("agentnexus.tools.web_fetch._get_client")
    def test_single_url(self, mock_get_client):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.extract.return_value = {
            "results": [
                {"url": "https://example.com", "title": "Example", "content": "Hello world"}
            ],
            "failed_results": [],
        }
        results = web_fetch_structured("https://example.com")
        assert len(results) == 1
        assert results[0]["url"] == "https://example.com"
        assert results[0]["content"] == "Hello world"
        call_kwargs = mock_client.extract.call_args[1]
        assert call_kwargs["urls"] == ["https://example.com"]

    @patch("agentnexus.tools.web_fetch._get_client")
    def test_multiple_urls(self, mock_get_client):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.extract.return_value = {
            "results": [
                {"url": "https://a.com", "title": "A", "content": "content A"},
                {"url": "https://b.com", "title": "B", "content": "content B"},
            ],
            "failed_results": [],
        }
        results = web_fetch_structured(["https://a.com", "https://b.com"])
        assert len(results) == 2
        call_kwargs = mock_client.extract.call_args[1]
        assert call_kwargs["urls"] == ["https://a.com", "https://b.com"]

    @patch("agentnexus.tools.web_fetch._get_client")
    def test_with_extract_depth(self, mock_get_client):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.extract.return_value = {"results": [], "failed_results": []}
        web_fetch_structured("https://example.com", extract_depth="advanced")
        call_kwargs = mock_client.extract.call_args[1]
        assert call_kwargs["extract_depth"] == "advanced"

    @patch("agentnexus.tools.web_fetch._get_client")
    def test_with_format(self, mock_get_client):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.extract.return_value = {"results": [], "failed_results": []}
        web_fetch_structured("https://example.com", fmt="text")
        call_kwargs = mock_client.extract.call_args[1]
        assert call_kwargs["format"] == "text"

    @patch("agentnexus.tools.web_fetch._get_client")
    def test_failed_results_attached(self, mock_get_client):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.extract.return_value = {
            "results": [
                {"url": "https://ok.com", "title": "OK", "content": "good"}
            ],
            "failed_results": [
                {"url": "https://fail.com", "error": "timeout"}
            ],
        }
        results = web_fetch_structured(["https://ok.com", "https://fail.com"])
        assert len(results) == 1
        assert results[0]["_failed_urls"] == ["https://fail.com"]

    @patch("agentnexus.tools.web_fetch._get_client")
    def test_all_failed(self, mock_get_client):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.extract.return_value = {
            "results": [],
            "failed_results": [
                {"url": "https://fail.com", "error": "timeout"}
            ],
        }
        results = web_fetch_structured("https://fail.com")
        assert len(results) == 1
        assert results[0]["_failed_urls"] == ["https://fail.com"]

    @patch("agentnexus.tools.web_fetch._get_client")
    def test_empty_urls(self, mock_get_client):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        results = web_fetch_structured([])
        assert results == []
        mock_client.extract.assert_not_called()

    @patch("agentnexus.tools.web_fetch._get_client")
    def test_no_client(self, mock_get_client):
        mock_get_client.return_value = None
        results = web_fetch_structured("https://example.com")
        assert results == []


class TestWebFetch:
    def setup_method(self):
        _cache.clear()

    @patch("agentnexus.tools.web_fetch.web_fetch_structured")
    def test_no_client_returns_config_msg(self, mock_ws):
        with patch("agentnexus.tools.web_fetch._get_client", return_value=None):
            mock_ws.return_value = []
            result = web_fetch("https://example.com")
            assert "未配置" in result

    @patch("agentnexus.tools.web_fetch.web_fetch_structured")
    def test_empty_results_returns_error(self, mock_ws):
        with patch("agentnexus.tools.web_fetch._get_client", return_value=MagicMock()):
            mock_ws.return_value = []
            result = web_fetch("https://example.com")
            assert "未能抓取" in result

    @patch("agentnexus.tools.web_fetch.web_fetch_structured")
    def test_single_url_formatted(self, mock_ws):
        mock_ws.return_value = [
            {"url": "https://example.com", "title": "Example", "content": "Hello"}
        ]
        result = web_fetch("https://example.com")
        assert "[Example]" in result
        assert "URL: https://example.com" in result
        assert "Hello" in result

    @patch("agentnexus.tools.web_fetch.web_fetch_structured")
    def test_failed_urls_shown(self, mock_ws):
        mock_ws.return_value = [
            {"url": "https://ok.com", "title": "OK", "content": "good",
             "_failed_urls": ["https://fail.com"]}
        ]
        result = web_fetch(["https://ok.com", "https://fail.com"])
        assert "抓取失败" in result
        assert "https://fail.com" in result

    def test_invalid_format_fallback(self):
        with patch("agentnexus.tools.web_fetch.web_fetch_structured") as mock_ws:
            mock_ws.return_value = [{"url": "https://example.com", "title": "T", "content": "C"}]
            with patch("agentnexus.tools.web_fetch._get_client", return_value=MagicMock()):
                result = web_fetch("https://example.com", format="invalid")
                call_kwargs = mock_ws.call_args[1]
                assert call_kwargs["fmt"] == "markdown"

    def test_invalid_depth_fallback(self):
        with patch("agentnexus.tools.web_fetch.web_fetch_structured") as mock_ws:
            mock_ws.return_value = [{"url": "https://example.com", "title": "T", "content": "C"}]
            with patch("agentnexus.tools.web_fetch._get_client", return_value=MagicMock()):
                result = web_fetch("https://example.com", extract_depth="invalid")
                call_kwargs = mock_ws.call_args[1]
                assert call_kwargs["extract_depth"] is None

    @patch("agentnexus.tools.web_fetch._get_client")
    def test_cache_hit_skips_api_call(self, mock_get_client):
        _cache.clear()
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.extract.return_value = {
            "results": [
                {"url": "https://cached.com", "title": "Cached", "content": "content"}
            ],
            "failed_results": [],
        }
        # First call hits API
        r1 = web_fetch("https://cached.com")
        assert mock_client.extract.call_count == 1
        # Second call should use cache
        r2 = web_fetch("https://cached.com")
        assert mock_client.extract.call_count == 1
        assert r1 == r2
        _cache.clear()
