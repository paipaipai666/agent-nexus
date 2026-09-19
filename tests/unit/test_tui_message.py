"""Tests for ChatMessage, ToolCall widgets and _safe helper."""

from unittest.mock import MagicMock, patch

from rich.text import Text
from textual.widgets import Label, Static

from agentnexus.tui.widgets.message import ChatMessage, ToolCall, _safe


class TestSafe:
    def test_empty(self):
        assert _safe(None) == Text("")
        assert _safe("") == Text("")

    def test_string(self):
        result = _safe("hello")
        assert isinstance(result, Text)
        assert result.plain == "hello"


class TestChatMessage:
    def test_init(self):
        msg = ChatMessage("user", "hello")
        assert msg.content == "hello"
        assert "user" in msg.classes
        assert not msg._rich_markup

    def test_init_markup(self):
        msg = ChatMessage("assistant", "<b>bold</b>", markup=True)
        assert msg._rich_markup is True

    def test_compose_no_code(self):
        msg = ChatMessage("user", "simple text")
        children = list(msg.compose())
        assert len(children) == 1
        assert isinstance(children[0], Static)
        assert children[0].id == "msg-content"

    def test_compose_with_code(self):
        msg = ChatMessage("assistant", "text```python\nprint('hi')\n```more")
        children = list(msg.compose())
        assert len(children) == 1
        assert isinstance(children[0], Static)
        assert children[0].id == "msg-content"


class TestAttachReaction:
    def test_attach_mounts_reaction_row(self):
        msg = ChatMessage("user", "hello")
        with patch.object(msg, "mount") as mock_mount:
            msg.attach_reaction("👍", "这题有意思")
        assert mock_mount.call_count == 1
        row = mock_mount.call_args[0][0]
        assert isinstance(row, Static)
        assert "msg-reaction" in row.classes
        assert row.content.plain == "👍 这题有意思"

    def test_attach_without_comment(self):
        msg = ChatMessage("user", "hello")
        with patch.object(msg, "mount") as mock_mount:
            msg.attach_reaction("🤩")
        row = mock_mount.call_args[0][0]
        assert row.content.plain == "🤩"

    def test_empty_emoji_is_noop(self):
        msg = ChatMessage("user", "hello")
        with patch.object(msg, "mount") as mock_mount:
            msg.attach_reaction("")
        mock_mount.assert_not_called()


class TestToolCall:
    def test_init(self):
        tc = ToolCall("bash", "ok", 150)
        assert tc.tool_name == "bash"
        assert tc.result == "ok"
        assert tc.duration_ms == 150

    def test_init_no_duration(self):
        tc = ToolCall("read")
        assert tc.tool_name == "read"
        assert tc.result == ""
        assert tc.duration_ms == 0

    def test_update_result(self):
        tc = ToolCall("bash", "old", 100)
        with patch.object(tc, "query_one", return_value=MagicMock()):
            tc.update_result("new", 200)
        assert tc.result == "new"
        assert tc.duration_ms == 200

    def test_compose(self):
        tc = ToolCall("bash", "ok", 150)
        children = list(tc.compose())
        assert len(children) == 3
        assert isinstance(children[0], Label)
        assert children[0].id == "tool-name"
        assert isinstance(children[1], Static)
        assert children[1].id == "tool-result"
        assert isinstance(children[2], Label)
        assert children[2].id == "tool-meta"
