"""Tests for JSON response parsing and auto-fix."""

import pytest
from agentnexus.agents.re_act_agent import ReActAgent


class TestJsonResponseParsing:
    def test_parse_tool_call(self):
        """Valid JSON tool call"""
        result = ReActAgent._parse_json_response(
            '{"tool": "web_search", "params": {"query": "北京天气"}}'
        )
        assert result["type"] == "tool_call"
        assert result["tool"] == "web_search"
        assert result["params"] == {"query": "北京天气"}

    def test_parse_answer(self):
        """Valid JSON answer"""
        result = ReActAgent._parse_json_response(
            '{"answer": "最终答案是42"}'
        )
        assert result["type"] == "answer"
        assert result["text"] == "最终答案是42"

    def test_parse_not_json(self):
        """Plain text → error"""
        result = ReActAgent._parse_json_response("这不是JSON")
        assert result["type"] == "error"

    def test_parse_missing_key(self):
        """JSON object without tool or answer key"""
        result = ReActAgent._parse_json_response('{"foo": "bar", "baz": 1}')
        assert result["type"] == "error"

    def test_parse_single_key_ambiguous(self):
        """Single-key JSON without tool/answer → treated as answer"""
        result = ReActAgent._parse_json_response('{"summary": "something"}')
        assert result["type"] == "answer"

    def test_parse_empty(self):
        result = ReActAgent._parse_json_response("")
        assert result["type"] == "error"

    def test_parse_params_not_dict(self):
        """params is not a dict → should default to empty dict"""
        result = ReActAgent._parse_json_response(
            '{"tool": "web_search", "params": "just a string"}'
        )
        assert result["type"] == "tool_call"
        assert result["params"] == {}


class TestJsonAutoFix:
    def test_fix_text_after_brace(self):
        """Text after closing brace → truncated"""
        result = ReActAgent._try_fix_json(
            '{"tool": "search", "params": {}} some extra text'
        )
        assert result == {"tool": "search", "params": {}}

    def test_fix_trailing_comma(self):
        """Trailing comma before closing brace → removed"""
        result = ReActAgent._try_fix_json(
            '{"tool": "search", "params": {"q": "test",}}'
        )
        assert result == {"tool": "search", "params": {"q": "test"}}

    def test_fix_missing_closing_brace(self):
        """Missing closing brace → appended"""
        result = ReActAgent._try_fix_json(
            '{"answer": "hello world"'
        )
        assert result == {"answer": "hello world"}

    def test_fix_no_braces(self):
        """No braces at all → None"""
        result = ReActAgent._try_fix_json("no json here")
        assert result is None

    def test_fix_empty_string(self):
        result = ReActAgent._try_fix_json("")
        assert result is None

    def test_fix_nested_trailing_comma(self):
        """Trailing comma in nested object"""
        result = ReActAgent._try_fix_json(
            '{"tool": "search", "params": {"q": "test", "n": 1,}}'
        )
        assert result == {"tool": "search", "params": {"q": "test", "n": 1}}


class TestJsonFormatPrompt:
    def test_format_section_not_empty(self):
        section = ReActAgent._build_json_format_section()
        assert "tool" in section
        assert "answer" in section
        assert "JSON" in section
