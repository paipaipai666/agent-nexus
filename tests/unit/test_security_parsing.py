"""Input parsing security tests — JSON robustness, STM deserialisation,
grep_search glob/pattern injection vectors."""

from unittest.mock import patch

from agentnexus.agents.re_act_agent import ReActAgent
from agentnexus.memory.short_term import ShortTermMemory


class TestRobustJsonParse:
    """ReActAgent._robust_json_parse with adversarial inputs."""

    def test_valid_tool_call(self):
        """Standard tool-call JSON is correctly classified."""
        result = ReActAgent._robust_json_parse(
            '{"tool": "file_read", "params": {"path": "test.txt"}}'
        )
        assert result["type"] == "tool_call"
        assert result["tool"] == "file_read"
        assert result["params"]["path"] == "test.txt"

    def test_injection_tool_call_routes_through_classify(self):
        """Shell-exec tool call is classified correctly (governance not bypassed)."""
        result = ReActAgent._robust_json_parse(
            '{"tool": "shell_exec", "params": {"command": "rm -rf /"}}'
        )
        assert result["type"] == "tool_call"
        assert result["tool"] == "shell_exec"
        assert result["params"]["command"] == "rm -rf /"

    def test_array_input_falls_to_error(self):
        """JSON array is not a dict → error classification."""
        result = ReActAgent._robust_json_parse('[{"tool": "shell_exec"}]')
        assert result["type"] == "error"

    def test_deeply_nested_json(self):
        """Deeply nested JSON does not cause stack overflow."""
        nested = "{" + '"a":' * 1000 + '"x"' + "}" * 1000
        result = ReActAgent._robust_json_parse(nested)
        assert result["type"] == "error"

    def test_empty_string(self):
        """Empty input returns error, not crash."""
        result = ReActAgent._robust_json_parse("")
        assert result["type"] == "error"

    def test_markdown_code_block(self):
        """JSON inside markdown code block is extracted."""
        text = "```json\n{\"tool\": \"search\", \"params\": {}}\n```"
        result = ReActAgent._robust_json_parse(text)
        assert result["type"] == "tool_call"

    def test_answer_only(self):
        """Answer-only JSON is classified as answer."""
        result = ReActAgent._robust_json_parse('{"answer": "Hello world"}')
        assert result["type"] == "answer"
        assert result["text"] == "Hello world"


class TestShortTermMemoryDeserialization:
    """ShortTermMemory.from_json with adversarial inputs."""

    def test_from_json_missing_keys(self):
        """Input missing 'messages' / 'summary' keys does not crash."""
        stm = ShortTermMemory.from_json('{"messages": []}')
        assert len(stm.get_all()) == 0

        stm2 = ShortTermMemory.from_json("{}")
        assert len(stm2.get_all()) == 0

    def test_from_json_wrong_types(self):
        """Non-list messages iterates string chars (doesn't crash)."""
        stm = ShortTermMemory.from_json('{"messages": "not a list"}')
        assert stm is not None
        assert isinstance(stm.get_all(), list)

    def test_from_json_very_long_content(self):
        """Very long message content restores without truncation."""
        long_content = "A" * 100000
        json_str = '{"messages": [{"role": "user", "content": "' + long_content + '"}]}'
        stm = ShortTermMemory.from_json(json_str)
        msgs = stm.get_all()
        assert len(msgs) == 1
        assert len(msgs[0]["content"]) == 100000

    def test_from_json_special_unicode(self):
        """Unicode special characters in JSON are preserved."""
        json_str = '{"messages": [{"role": "user", "content": "\\u0000\\u00ff\\u4e2d\\u6587"}]}'
        stm = ShortTermMemory.from_json(json_str)
        msgs = stm.get_all()
        assert "\u0000" in msgs[0]["content"]
        assert "\u4e2d\u6587" in msgs[0]["content"]


class TestGrepSearchInjection:
    """grep_search — glob/path injection vectors."""

    @patch("agentnexus.tools.grep_search.subprocess.run")
    @patch("agentnexus.tools.grep_search.grep_available", return_value=True)
    def test_grep_glob_path_traversal(self, mock_avail, mock_run):
        """Traversal in glob is passed to rg (rg handles safely with --glob)."""
        from agentnexus.tools.grep_search import grep_search

        mock_run.return_value.returncode = 1
        mock_run.return_value.stdout = ""
        result = grep_search("test", glob="../etc/passwd", literal=True)
        assert "未找到" in result or "error" in result.lower()
        args = mock_run.call_args[0][0]
        assert "--glob" in args
        assert "../etc/passwd" in args[args.index("--glob") + 1]

    @patch("agentnexus.tools.grep_search.subprocess.run")
    @patch("agentnexus.tools.grep_search.grep_available", return_value=True)
    def test_grep_pattern_regex_injection(self, mock_avail, mock_run):
        """Regex in literal mode is still passed via -e (safe)."""
        from agentnexus.tools.grep_search import grep_search

        mock_run.return_value.returncode = 1
        mock_run.return_value.stdout = ""
        result = grep_search(".*", literal=True)
        assert "未找到" in result or "error" in result.lower()
        args = mock_run.call_args[0][0]
        assert "--fixed-strings" in args

    @patch("agentnexus.tools.grep_search.subprocess.run")
    @patch("agentnexus.tools.grep_search.grep_available", return_value=True)
    def test_grep_path_with_options(self, mock_avail, mock_run):
        """Path containing -- options is passed after -e (safe positional)."""
        from agentnexus.tools.grep_search import grep_search

        mock_run.return_value.returncode = 1
        mock_run.return_value.stdout = ""
        result = grep_search("test", path="--some-option", literal=True)
        assert "未找到" in result or "error" in result.lower()
        args = mock_run.call_args[0][0]
        assert args[-1] == "--some-option"

    @patch("agentnexus.tools.grep_search.grep_available", return_value=True)
    def test_grep_empty_pattern(self, mock_run):
        """Pattern shorter than 2 chars returns early without rg call."""
        from agentnexus.tools.grep_search import grep_search

        with patch("agentnexus.tools.grep_search.subprocess.run") as mock_run2:
            result = grep_search("x", literal=True)
            assert "至少需要2个字符" in result
            mock_run2.assert_not_called()
