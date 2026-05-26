"""Tests for agentnexus.tools.grep_search."""

from unittest.mock import MagicMock, patch

import pytest

from agentnexus.tools.grep_search import (
    _glob_to_regex,
    _normalize_glob_pattern,
    grep_available,
    grep_search,
)


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


class TestNormalizeGlobPattern:
    def test_simple_star(self):
        assert _normalize_glob_pattern("*.py") == "*.py"

    def test_double_star_prefix(self):
        assert _normalize_glob_pattern("**/*.py") == "*.py"

    def test_double_star_suffix(self):
        assert _normalize_glob_pattern("src/**") == "src"

    def test_double_star_both(self):
        assert _normalize_glob_pattern("**/*.py") == "*.py"

    def test_no_double_star(self):
        assert _normalize_glob_pattern("test_*") == "test_*"


class TestGlobToRegex:
    @pytest.mark.parametrize(
        "pattern,should_match,should_not_match",
        [
            ("*.py", ["test.py", "a/b.py", "a/b/c.py"], ["test.txt", "test.pyc"]),
            ("test_*", ["test_file.py", "test_.txt", "a/test_file.py"], ["my_test.py"]),
            ("[abc].py", ["a.py", "b.py", "c.py", "a/b.py"], ["d.py", "ab.py"]),
            ("[!abc].py", ["d.py", "x.py"], ["a.py", "b.py"]),
            ("?.py", ["a.py", "b.py", "a/b.py"], ["ab.py", ".py"]),
        ],
    )
    def test_glob_patterns(self, pattern, should_match, should_not_match):
        regex = _glob_to_regex(pattern)
        for text in should_match:
            assert regex.match(text), f"'{pattern}' should match '{text}'"
        for text in should_not_match:
            assert not regex.match(text), f"'{pattern}' should not match '{text}'"

    def test_double_star_matches_path(self):
        regex = _glob_to_regex("**/test_*")
        assert regex.match("test_file.py")
        assert regex.match("src/test_file.py")
        assert regex.match("src/sub/test_file.py")
        assert not regex.match("my_test.py")


class TestGrepSearchGlobFallback:
    """Test glob matching in Python fallback (ripgrep not available)."""

    @patch("agentnexus.tools.grep_search.grep_available", return_value=False)
    def test_glob_star_py(self, mock_avail, tmp_path):
        """*.py should match all .py files in any directory."""
        (tmp_path / "a.py").write_text("hello world")
        (tmp_path / "b.txt").write_text("hello world")
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "c.py").write_text("hello world")
        (sub / "d.txt").write_text("hello world")

        result = grep_search("hello", path=str(tmp_path), glob="*.py", max_results=10)
        assert "a.py" in result
        assert "c.py" in result
        assert "b.txt" not in result
        assert "d.txt" not in result

    @patch("agentnexus.tools.grep_search.grep_available", return_value=False)
    def test_glob_double_star_py(self, mock_avail, tmp_path):
        """**/*.py should behave the same as *.py."""
        (tmp_path / "a.py").write_text("hello world")
        (tmp_path / "b.txt").write_text("hello world")
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "c.py").write_text("hello world")

        result = grep_search("hello", path=str(tmp_path), glob="**/*.py", max_results=10)
        assert "a.py" in result
        assert "c.py" in result
        assert "b.txt" not in result

    @patch("agentnexus.tools.grep_search.grep_available", return_value=False)
    def test_glob_prefix_pattern(self, mock_avail, tmp_path):
        """test_* should match all test_* files in any directory."""
        (tmp_path / "test_file.py").write_text("hello world")
        (tmp_path / "other.py").write_text("hello world")
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "test_module.py").write_text("hello world")
        (sub / "helper.py").write_text("hello world")

        result = grep_search("hello", path=str(tmp_path), glob="test_*", max_results=10)
        assert "test_file.py" in result
        assert "test_module.py" in result
        assert "other.py" not in result
        assert "helper.py" not in result

    @patch("agentnexus.tools.grep_search.grep_available", return_value=False)
    def test_glob_double_star_prefix(self, mock_avail, tmp_path):
        """**/test_* should behave the same as test_*."""
        (tmp_path / "test_file.py").write_text("hello world")
        (tmp_path / "other.py").write_text("hello world")
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "test_module.py").write_text("hello world")

        result = grep_search("hello", path=str(tmp_path), glob="**/test_*", max_results=10)
        assert "test_file.py" in result
        assert "test_module.py" in result
        assert "other.py" not in result
