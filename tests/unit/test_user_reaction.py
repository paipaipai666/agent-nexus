"""Tests for the express_reaction entertainment tool and its GUI event rewrite.

Covers three layers:
1. Tool body — enum validation, comment truncation/normalization.
2. Provider registration — gated by settings.enable_user_reaction (default off).
3. GUI event mapping — tool_start rewritten to user_reaction, tool_done skipped.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from agentnexus.tools.user_reaction import (
    MAX_COMMENT_LEN,
    REACTION_EMOJI,
    express_reaction,
    parse_reaction_arguments,
)


class TestExpressReactionTool:
    def test_valid_reaction(self):
        result = express_reaction("like")
        assert "已表达" in result
        assert REACTION_EMOJI["like"] in result

    def test_case_and_whitespace_normalized(self):
        result = express_reaction("  Excited ")
        assert REACTION_EMOJI["excited"] in result

    def test_invalid_reaction_lists_valid_values(self):
        result = express_reaction("shrug")
        assert "无效反应" in result
        for name in REACTION_EMOJI:
            assert name in result

    def test_comment_kept(self):
        result = express_reaction("surprised", comment="这脑洞可以")
        assert "这脑洞可以" in result

    def test_comment_truncated(self):
        long_comment = "啊" * (MAX_COMMENT_LEN + 20)
        result = express_reaction("sleepy", comment=long_comment)
        assert ("啊" * MAX_COMMENT_LEN) in result
        assert ("啊" * (MAX_COMMENT_LEN + 1)) not in result

    def test_comment_whitespace_collapsed(self):
        result = express_reaction("confused", comment="a\n\n  b\tc")
        assert "a b c" in result

    def test_no_side_effects(self):
        # Pure function: same input, same output, no state touched.
        a = express_reaction("like", comment="x")
        b = express_reaction("like", comment="x")
        assert a == b


class TestParseReactionArguments:
    def test_dict_arguments(self):
        assert parse_reaction_arguments({"reaction": "Like", "comment": "ok"}) == ("like", "ok")

    def test_json_string_arguments(self):
        assert parse_reaction_arguments('{"reaction": "excited"}') == ("excited", "")

    def test_missing_reaction(self):
        assert parse_reaction_arguments({}) == ("", "")

    def test_malformed_input_never_raises(self):
        for bad in (None, "not json", 42, ["like"], "{broken"):
            name, comment = parse_reaction_arguments(bad)
            assert name == ""
            assert comment == ""

    def test_comment_truncated_and_normalized(self):
        name, comment = parse_reaction_arguments(
            {"reaction": "sleepy", "comment": "a\n\n  b" + "啊" * 80}
        )
        assert name == "sleepy"
        assert comment.startswith("a b")
        assert len(comment) <= MAX_COMMENT_LEN


class TestReactionProvider:
    def _register(self):
        from agentnexus.tools.providers.reaction_provider import ReactionToolProvider
        from agentnexus.tools.providers.base import ToolProviderContext

        executor = MagicMock()
        executor.list_tools.return_value = []
        ctx = ToolProviderContext()
        ReactionToolProvider().register(executor, ctx)
        return executor

    @patch("agentnexus.core.config.get_settings")
    def test_disabled_by_default(self, mock_settings):
        mock_settings.return_value = SimpleNamespace(enable_user_reaction=False)
        executor = self._register()
        executor.register_tool.assert_not_called()

    @patch("agentnexus.core.config.get_settings")
    def test_enabled_registers_tool(self, mock_settings):
        mock_settings.return_value = SimpleNamespace(enable_user_reaction=True)
        executor = self._register()
        assert executor.register_tool.call_count == 1
        name = executor.register_tool.call_args[0][0]
        assert name == "express_reaction"

    @patch("agentnexus.core.config.get_settings")
    def test_settings_error_disables(self, mock_settings):
        mock_settings.side_effect = RuntimeError("boom")
        executor = self._register()
        executor.register_tool.assert_not_called()


class TestGuiEventOverride:
    def _map(self, event_type, payload):
        from agentnexus.server.routes.chat import _map_to_gui_event

        event = SimpleNamespace(type=event_type, payload=payload, run_id="r1")
        return _map_to_gui_event(event, chat_service=MagicMock(), seq=1)

    def test_tool_start_rewritten_to_user_reaction(self):
        ev = self._map("tool_start", {
            "name": "express_reaction",
            "arguments": {"reaction": "excited", "comment": "这题有意思"},
        })
        assert ev is not None
        assert ev["type"] == "user_reaction"
        assert ev["reaction"] == "excited"
        assert ev["emoji"] == REACTION_EMOJI["excited"]
        assert ev["comment"] == "这题有意思"
        assert ev["run_id"] == "r1"

    def test_tool_start_with_json_string_arguments(self):
        ev = self._map("tool_start", {
            "name": "express_reaction",
            "arguments": '{"reaction": "like"}',
        })
        assert ev is not None
        assert ev["type"] == "user_reaction"
        assert ev["emoji"] == REACTION_EMOJI["like"]
        assert ev["comment"] == ""

    def test_unknown_reaction_yields_empty_emoji(self):
        ev = self._map("tool_start", {
            "name": "express_reaction",
            "arguments": {"reaction": "shrug"},
        })
        assert ev is not None
        assert ev["type"] == "user_reaction"
        assert ev["emoji"] == ""
        assert ev["reaction"] == "shrug"

    def test_tool_done_skipped(self):
        ev = self._map("tool_done", {
            "name": "express_reaction",
            "arguments": {},
            "result": "[express_reaction] 已表达 👍",
        })
        assert ev is None

    def test_normal_tool_unaffected(self):
        ev = self._map("tool_start", {
            "name": "web_search",
            "arguments": {"query": "foo"},
        })
        assert ev is not None
        assert ev["type"] == "tool_call"
        assert ev["tool_name"] == "web_search"

        ev = self._map("tool_done", {
            "name": "web_search",
            "arguments": {},
            "result": "hits",
        })
        assert ev is not None
        assert ev["type"] == "tool_result"
