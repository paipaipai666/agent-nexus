"""Tests for agentnexus.tools.grep_search."""

from unittest.mock import MagicMock, patch

from agentnexus.tools.grep_search import grep_available, grep_search


class TestGrepAvailable:
    @patch("agentnexus.tools.grep_search.subprocess.run")
    def test_available(self, mock_run):
        mock_run.return_value.returncode = 0
        assert grep_available() is True

    @patch("agentnexus.tools.grep_search.subprocess.run", side_effect=FileNotFoundError)
    def test_not_available(self, mock_run):
        assert grep_available() is False


class TestGrepSearch:
    def test_short_pattern(self):
        result = grep_search("a")
        assert "至少需要2个字符" in result

    @patch("agentnexus.tools.grep_search.grep_available", return_value=False)
    def test_rg_not_available(self, mock_avail):
        result = grep_search("hello")
        assert "未安装" in result

    @patch("agentnexus.tools.grep_search.grep_available", return_value=True)
    @patch("agentnexus.tools.grep_search.subprocess.run")
    def test_no_results(self, mock_run, mock_avail):
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_run.return_value = mock_result
        result = grep_search("nonexistent_pattern_xyz")
        assert "未找到" in result

    @patch("agentnexus.tools.grep_search.grep_available", return_value=True)
    @patch("agentnexus.tools.grep_search.subprocess.run")
    def test_with_results(self, mock_run, mock_avail):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "file.py:10:def hello():\nfile.py:20:    print(hello)"
        mock_run.return_value = mock_result
        result = grep_search("hello")
        assert "file.py:10" in result
        assert "grep 搜索结果" in result
