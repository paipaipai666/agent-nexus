"""Tests for agentnexus.prompts."""
from unittest.mock import patch

import agentnexus.prompts as prompts


class TestLoadPrompt:
    def test_load_prompt_loads_existing(self):
        content = prompts.load_prompt("react")
        assert isinstance(content, str)
        assert len(content) > 0

    def test_load_prompt_missing_raises(self):
        try:
            prompts.load_prompt("non_existent_file_xyz")
            assert False, "Expected FileNotFoundError"
        except FileNotFoundError:
            pass


class TestGetCurrentDate:
    def test_get_current_date_format(self):
        date = prompts.get_current_date()
        parts = date.split("-")
        assert len(parts) == 3
        assert len(parts[0]) == 4
        assert len(parts[1]) == 2
        assert len(parts[2]) == 2
        from datetime import datetime
        datetime.strptime(date, "%Y-%m-%d")


class TestFormatPrompt:
    def test_format_prompt_injects_date(self):
        expected = prompts.get_current_date()
        with patch("agentnexus.prompts.load_prompt", return_value="Date: {date}"):
            result = prompts.format_prompt("test_template")
            assert result == f"Date: {expected}"

    def test_format_prompt_with_extra_kwargs(self):
        with patch("agentnexus.prompts.load_prompt", return_value="{date}: {user} says {msg}"):
            result = prompts.format_prompt("test", user="Alice", msg="Hello")
        parts = result.split(": ", 1)
        from datetime import datetime
        datetime.strptime(parts[0], "%Y-%m-%d")
        assert parts[1] == "Alice says Hello"

    def test_format_prompt_custom_date(self):
        with patch("agentnexus.prompts.load_prompt", return_value="Date: {date}"):
            result = prompts.format_prompt("test", date="2024-06-01")
            assert result == "Date: 2024-06-01"
