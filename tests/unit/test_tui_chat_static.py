"""Static method tests for ChatScreen: _condense_search_result,
_condense_file_result, _format_subagent_result."""

import json

from agentnexus.tui.screens.chat import ChatScreen


class TestCondenseSearchResult:
    def test_basic_search_result(self):
        """Only title/score lines and URLs are kept; body content removed."""
        text = (
            "[1] Title (2024-01-01) [相关度: 0.95]\n"
            "URL: https://example.com\n"
            "Some content body here..."
        )
        result = ChatScreen._condense_search_result(text)
        assert "[1]" in result
        assert "URL:" in result
        assert "content body" not in result

    def test_multiple_results(self):
        """All result items are kept; body between them is dropped."""
        text = (
            "[1] First\n"
            "URL: http://a.com\n"
            "body\n"
            "[2] Second\n"
            "URL: http://b.com\n"
            "body2"
        )
        result = ChatScreen._condense_search_result(text)
        assert "[1]" in result
        assert "[2]" in result
        assert "URL:" in result
        assert "body" not in result

    def test_no_matching_lines_returns_truncated(self):
        """When no title or URL lines exist, fallback to first 500 chars."""
        text = "just some random text without structure"
        result = ChatScreen._condense_search_result(text)
        assert len(result) == 500 or result == text[:500]
        assert result == text[:500]

    def test_empty_lines_skipped(self):
        """Blank lines between entries are omitted."""
        text = "[1] A\nURL: http://a.com\n\n\n[2] B\nURL: http://b.com"
        result = ChatScreen._condense_search_result(text)
        assert "\n\n\n" not in result

    def test_empty_string_returns_empty(self):
        """Empty input returns empty string (fails to match → fallback)."""
        result = ChatScreen._condense_search_result("")
        assert result == ""


class TestCondenseFileResult:
    def test_file_result(self):
        """Only the metadata line is kept when input starts with [文件]."""
        text = "[文件] path/to/file.py (10 行, 共 200 字节)\n1 | line1\n2 | line2"
        result = ChatScreen._condense_file_result(text)
        assert "[文件]" in result
        assert "line1" not in result
        assert "path/to/file.py" in result

    def test_non_file_result_truncated(self):
        """Non-file result is truncated to 200 characters."""
        text = "x" * 300
        result = ChatScreen._condense_file_result(text)
        assert len(result) == 200

    def test_empty_string(self):
        """Empty string returns empty string."""
        assert ChatScreen._condense_file_result("") == ""

    def test_unicode_content_not_truncated_when_short(self):
        """Short non-file content under 200 chars is returned as-is."""
        text = "简短回复"
        result = ChatScreen._condense_file_result(text)
        assert result == "简短回复"


class TestFormatSubagentResult:
    def test_valid_json(self):
        """Valid JSON payload is formatted into a readable summary."""
        text = json.dumps({
            "role": "coder",
            "status": "success",
            "steps_used": 5,
            "answer": "done",
            "summary": "",
            "allowed_tools": ["bash", "read"],
        }, ensure_ascii=False)
        result = ChatScreen._format_subagent_result(text)
        assert "子代理" in result
        assert "role=coder" in result
        assert "status=success" in result
        assert "steps=5" in result
        assert "bash" in result
        assert "answer: done" in result

    def test_invalid_json_returns_raw_text_truncated(self):
        """Invalid JSON returns first 500 characters of raw text."""
        text = "not json"
        result = ChatScreen._format_subagent_result(text)
        assert result == "not json"

    def test_non_dict_json_returns_raw_text_truncated(self):
        """JSON list (not dict) returns first 500 chars of raw text."""
        text = json.dumps([1, 2, 3])
        result = ChatScreen._format_subagent_result(text)
        assert result == "[1, 2, 3]"

    def test_empty_answer_uses_summary(self):
        """When answer is empty, summary is shown instead."""
        text = json.dumps({
            "role": "researcher",
            "status": "done",
            "answer": "",
            "summary": "found info",
        }, ensure_ascii=False)
        result = ChatScreen._format_subagent_result(text)
        assert "researcher" in result
        assert "summary: found info" in result

    def test_no_answer_or_summary(self):
        """When both answer and summary are empty, only header lines shown."""
        text = json.dumps({
            "role": "reader",
            "status": "ok",
            "answer": "",
            "summary": "",
        }, ensure_ascii=False)
        result = ChatScreen._format_subagent_result(text)
        assert "role=reader" in result
        assert "answer:" not in result
        assert "summary:" not in result

    def test_allowed_tools_empty_shows_dash(self):
        """Empty allowed_tools list shows a dash placeholder."""
        text = json.dumps({
            "role": "general",
            "status": "ok",
            "allowed_tools": [],
            "answer": "",
            "summary": "",
        }, ensure_ascii=False)
        result = ChatScreen._format_subagent_result(text)
        assert "tools: -" in result

    def test_answer_truncated_at_400_chars(self):
        """Answer text is truncated to 400 characters."""
        long_answer = "a" * 500
        text = json.dumps({
            "role": "coder",
            "status": "ok",
            "answer": long_answer,
            "summary": "",
        }, ensure_ascii=False)
        result = ChatScreen._format_subagent_result(text)
        # 400 chars of 'a' followed by nothing extra
        assert len(result.split("answer: ")[1]) == 400

    def test_missing_keys_default(self):
        """Missing keys get sensible defaults (role='general', status='unknown')."""
        text = json.dumps({}, ensure_ascii=False)
        result = ChatScreen._format_subagent_result(text)
        assert "role=general" in result
        assert "status=unknown" in result
        assert "steps=0" in result
