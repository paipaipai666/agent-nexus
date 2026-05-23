"""Tests for ChatScreen static methods: _condense_search_result,
_condense_file_result, _format_subagent_result."""

import json

from agentnexus.tui.screens.chat import ChatScreen


class TestCondenseSearchResult:
    def test_keeps_header_lines(self):
        text = "[1] Title\nURL: https://example.com\nbody content here"
        result = ChatScreen._condense_search_result(text)
        assert "[1]" in result
        assert "URL:" in result
        assert "body content" not in result

    def test_removes_body(self):
        text = (
            "[1] Result A\nURL: http://a.com\nsome body\n"
            "[2] Result B\nURL: http://b.com\nmore body"
        )
        result = ChatScreen._condense_search_result(text)
        assert "[1]" in result
        assert "[2]" in result
        assert "body" not in result

    def test_empty_result(self):
        assert ChatScreen._condense_search_result("") == ""

    def test_fallback_truncation(self):
        text = "no markers " + "x" * 500
        result = ChatScreen._condense_search_result(text)
        assert len(result) == 500
        assert result == text[:500]


class TestCondenseFileResult:
    def test_keeps_first_line(self):
        text = "[文件] path/to/file.py (10 行, 共 200 字节)\n1 | line1\n2 | line2"
        result = ChatScreen._condense_file_result(text)
        assert result == "[文件] path/to/file.py (10 行, 共 200 字节)"
        assert "line1" not in result

    def test_truncates_long(self):
        text = "x" * 300
        result = ChatScreen._condense_file_result(text)
        assert len(result) == 200

    def test_empty(self):
        assert ChatScreen._condense_file_result("") == ""


class TestFormatSubagentResult:
    def test_valid_json(self):
        text = json.dumps({
            "role": "coder",
            "status": "success",
            "steps_used": 5,
            "allowed_tools": ["bash", "read"],
            "answer": "done",
        })
        result = ChatScreen._format_subagent_result(text)
        assert "子代理" in result
        assert "role=coder" in result
        assert "status=success" in result
        assert "steps=5" in result
        assert "bash" in result
        assert "answer: done" in result

    def test_invalid_json(self):
        text = "this is not json"
        result = ChatScreen._format_subagent_result(text)
        assert result == "this is not json"

    def test_minimal_payload(self):
        text = json.dumps({})
        result = ChatScreen._format_subagent_result(text)
        assert "role=general" in result
        assert "status=unknown" in result
        assert "steps=0" in result
        assert "tools: -" in result

    def test_with_answer(self):
        text = json.dumps({
            "role": "researcher",
            "status": "ok",
            "answer": "found the answer",
            "summary": "ignored summary",
        })
        result = ChatScreen._format_subagent_result(text)
        assert "answer: found the answer" in result
        assert "summary:" not in result

    def test_with_summary(self):
        text = json.dumps({
            "role": "researcher",
            "status": "ok",
            "answer": "",
            "summary": "short summary",
        })
        result = ChatScreen._format_subagent_result(text)
        assert "summary: short summary" in result

    def test_with_allowed_tools(self):
        text = json.dumps({
            "role": "general",
            "status": "ok",
            "allowed_tools": ["bash", "python", "read"],
            "answer": "",
            "summary": "",
        })
        result = ChatScreen._format_subagent_result(text)
        assert "tools: bash, python, read" in result
