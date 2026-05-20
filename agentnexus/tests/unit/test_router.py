"""Tests for grep_search tool"""
import pytest
from agentnexus.tools.grep_search import grep_search, grep_available


class TestGrepSearch:
    def test_rejects_short_pattern(self):
        result = grep_search("x")
        assert "至少需要2个字符" in result

    def test_rejects_empty_pattern(self):
        result = grep_search("")
        assert "至少需要2个字符" in result

    def test_reports_unavailable_when_rg_missing(self, mocker):
        mocker.patch("agentnexus.tools.grep_search.grep_available", return_value=False)
        result = grep_search("some_function")
        assert "未安装" in result

    def test_grep_available_returns_bool(self):
        result = grep_available()
        assert isinstance(result, bool)
