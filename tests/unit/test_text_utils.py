"""Tests for agentnexus.core.text_utils.collapse_and_truncate."""


from agentnexus.core.text_utils import collapse_and_truncate


class TestCollapseAndTruncate:
    def test_normal_input_collapses_multiple_spaces(self):
        result = collapse_and_truncate("hello     world", 100)
        assert result == "hello world"

    def test_tabs_and_newlines_collapsed_to_single_spaces(self):
        result = collapse_and_truncate("hello\t\t\n\n  world", 100)
        assert result == "hello world"

    def test_mixed_whitespace_collapsed(self):
        result = collapse_and_truncate("  a \t\n b \r\n c  ", 100)
        assert result == "a b c"

    def test_input_within_limit_no_truncation(self):
        text = "short"
        result = collapse_and_truncate(text, 10)
        assert result == "short"
        assert len(result) == 5

    def test_input_exactly_at_limit_no_truncation(self):
        text = "abcde"
        result = collapse_and_truncate(text, 5)
        assert result == "abcde"

    def test_input_one_char_over_limit(self):
        text = "abcdef"
        result = collapse_and_truncate(text, 5)
        assert result == "abcd…"

    def test_text_none_returns_empty_string(self):
        result = collapse_and_truncate(None, 100)
        assert result == ""

    def test_text_empty_string_returns_empty_string(self):
        result = collapse_and_truncate("", 100)
        assert result == ""

    def test_limit_zero_returns_ellipsis(self):
        # max(0, 0 - 1) = max(0, -1) = 0, so [:0] + "…"
        result = collapse_and_truncate("hello", 0)
        assert result == "…"

    def test_limit_one_returns_ellipsis(self):
        # max(0, 1 - 1) = max(0, 0) = 0, so [:0] + "…"
        result = collapse_and_truncate("hello", 1)
        assert result == "…"

    def test_limit_two_returns_first_char_plus_ellipsis(self):
        result = collapse_and_truncate("hello", 2)
        assert result == "h…"

    def test_limit_larger_than_input_no_truncation(self):
        result = collapse_and_truncate("hi", 1000)
        assert result == "hi"

    def test_only_whitespace_returns_empty_string(self):
        result = collapse_and_truncate("   \t\n   ", 100)
        assert result == ""

    def test_unicode_characters(self):
        result = collapse_and_truncate("こんにちは世界", 5)
        # Each char is one character in Python's len()
        assert result == "こんにち…"

    def test_unicode_within_limit(self):
        result = collapse_and_truncate("你好", 10)
        assert result == "你好"

    def test_multibyte_emoji(self):
        result = collapse_and_truncate("hello 🌍 world", 100)
        assert result == "hello 🌍 world"

    def test_numeric_input_as_text(self):
        result = collapse_and_truncate(12345, 100)
        assert result == "12345"

    def test_empty_after_collapse(self):
        result = collapse_and_truncate("     ", 10)
        assert result == ""

    def test_single_word_no_collapse_needed(self):
        result = collapse_and_truncate("hello", 100)
        assert result == "hello"

    def test_long_text_truncation(self):
        text = "a" * 1000
        result = collapse_and_truncate(text, 10)
        assert len(result) == 10
        assert result.endswith("…")
        assert result == "a" * 9 + "…"

    def test_truncated_content_preserves_prefix(self):
        text = "abcdefghijklmnop"
        result = collapse_and_truncate(text, 6)
        assert result == "abcde…"
