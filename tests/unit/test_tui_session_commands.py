"""Tests for TUI session management commands."""

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def mock_chat_screen():
    """Create a mock ChatScreen for testing."""
    from agentnexus.tui.screens.chat import ChatScreen

    # Mock dependencies
    agent = MagicMock()
    memory = MagicMock()
    version = MagicMock()
    version.session_id = "test_session"

    screen = ChatScreen(agent, memory, version)
    screen._chat_area = MagicMock()
    screen._version = version

    return screen


# ── /sessions command tests ──────────────────────────────────────────


def test_handle_sessions_no_version(mock_chat_screen):
    """Test /sessions command when version manager is not available."""
    mock_chat_screen._version = None
    mock_chat_screen._handle_sessions()
    mock_chat_screen._chat_area.add_system.assert_called_with("[red]版本管理未启用[/]")


def test_handle_sessions_no_sessions(mock_chat_screen):
    """Test /sessions command when no sessions exist."""
    with patch(
        "agentnexus.memory.versioned.ConversationVersionManager.find_recent_sessions",
        return_value=[],
    ):
        mock_chat_screen._handle_sessions()
        mock_chat_screen._chat_area.add_system.assert_any_call(
            "[yellow]当前目录下没有找到历史会话[/]"
        )


def test_handle_sessions_with_sessions(mock_chat_screen):
    """Test /sessions command with existing sessions."""
    mock_sessions = [
        {
            "session_id": "session_1",
            "last_message_at": "2026-05-30 10:00:00",
            "preview": "Test question 1",
        },
        {
            "session_id": "session_2",
            "last_message_at": "2026-05-30 11:00:00",
            "preview": "Test question 2",
        },
    ]
    with patch(
        "agentnexus.memory.versioned.ConversationVersionManager.find_recent_sessions",
        return_value=mock_sessions,
    ):
        mock_chat_screen._handle_sessions()
        # Should call add_system multiple times
        assert mock_chat_screen._chat_area.add_system.call_count >= 3


def test_handle_sessions_marks_current_session(mock_chat_screen):
    """Test that /sessions marks the current session."""
    mock_sessions = [
        {
            "session_id": "test_session",
            "last_message_at": "2026-05-30 10:00:00",
            "preview": "Current session",
        },
        {
            "session_id": "other_session",
            "last_message_at": "2026-05-30 11:00:00",
            "preview": "Other session",
        },
    ]
    with patch(
        "agentnexus.memory.versioned.ConversationVersionManager.find_recent_sessions",
        return_value=mock_sessions,
    ):
        mock_chat_screen._handle_sessions()
        # Should mark current session with "← 当前"
        calls = mock_chat_screen._chat_area.add_system.call_args_list
        current_marked = any("← 当前" in str(call) for call in calls)
        assert current_marked


# ── /switch command tests ──────────────────────────────────────────


def test_handle_switch_no_arg(mock_chat_screen):
    """Test /switch command with no argument."""
    mock_chat_screen._handle_switch("")
    mock_chat_screen._chat_area.add_system.assert_called_with(
        "[red]用法: /switch <session_id>[/]"
    )


def test_handle_switch_no_version(mock_chat_screen):
    """Test /switch command when version manager is not available."""
    mock_chat_screen._version = None
    mock_chat_screen._handle_switch("session_1")
    mock_chat_screen._chat_area.add_system.assert_called_with("[red]版本管理未启用[/]")


def test_handle_switch_session_not_found(mock_chat_screen):
    """Test /switch command with non-existent session."""
    with patch(
        "agentnexus.memory.versioned.ConversationVersionManager.session_belongs_to_workspace",
        return_value=False,
    ):
        mock_chat_screen._handle_switch("nonexistent")
        mock_chat_screen._chat_area.add_system.assert_called_with(
            "[red]会话不存在:[/red] nonexistent"
        )


def test_handle_switch_success(mock_chat_screen):
    """Test /switch command with valid session."""
    with patch(
        "agentnexus.memory.versioned.ConversationVersionManager.session_belongs_to_workspace",
        return_value=True,
    ):
        mock_chat_screen._restore_stm_from_version = MagicMock()
        mock_chat_screen._render_restored_history = MagicMock()
        mock_chat_screen._refresh_version_display = MagicMock()

        mock_chat_screen._handle_switch("session_1")

        # Should update session_id
        assert mock_chat_screen._version.session_id == "session_1"
        # Should call restore methods
        mock_chat_screen._restore_stm_from_version.assert_called_once()
        mock_chat_screen._render_restored_history.assert_called_once()
        mock_chat_screen._refresh_version_display.assert_called_once()


def test_handle_switch_back_and_forth(mock_chat_screen):
    """Test switching between sessions multiple times."""
    with patch(
        "agentnexus.memory.versioned.ConversationVersionManager.session_belongs_to_workspace",
        return_value=True,
    ):
        mock_chat_screen._restore_stm_from_version = MagicMock()
        mock_chat_screen._render_restored_history = MagicMock()
        mock_chat_screen._refresh_version_display = MagicMock()

        # Switch to session_1
        mock_chat_screen._handle_switch("session_1")
        assert mock_chat_screen._version.session_id == "session_1"

        # Switch to session_2
        mock_chat_screen._handle_switch("session_2")
        assert mock_chat_screen._version.session_id == "session_2"

        # Switch back to session_1
        mock_chat_screen._handle_switch("session_1")
        assert mock_chat_screen._version.session_id == "session_1"


# ── _render_restored_history tests ──────────────────────────────────


def test_render_restored_history_no_memory(mock_chat_screen):
    """Test restoring history when memory is None."""
    mock_chat_screen._memory = None
    mock_chat_screen._render_restored_history()
    mock_chat_screen._chat_area.add_system.assert_not_called()


def test_render_restored_history_no_short_term(mock_chat_screen):
    """Test restoring history when short_term is None."""
    mock_chat_screen._memory = MagicMock()
    mock_chat_screen._memory.short_term = None
    mock_chat_screen._render_restored_history()
    mock_chat_screen._chat_area.add_system.assert_not_called()


def test_render_restored_history_memory_exception(mock_chat_screen):
    """Test restoring history when memory raises exception."""
    mock_chat_screen._memory = MagicMock()
    mock_chat_screen._memory.short_term.get_all.side_effect = Exception("Memory error")
    mock_chat_screen._render_restored_history()
    # Should not raise, just return silently
    mock_chat_screen._chat_area.add_system.assert_not_called()


def test_render_restored_history_empty_messages(mock_chat_screen):
    """Test restoring history with empty message list."""
    mock_chat_screen._memory = MagicMock()
    mock_chat_screen._memory.short_term.get_all.return_value = []
    mock_chat_screen._render_restored_history()
    mock_chat_screen._chat_area.add_system.assert_not_called()


def test_render_restored_history_only_empty_content(mock_chat_screen):
    """Test restoring history with messages that have empty content."""
    mock_messages = [
        {"role": "user", "content": ""},
        {"role": "assistant", "content": None},
        {"role": "tool", "content": ""},
    ]
    mock_chat_screen._memory = MagicMock()
    mock_chat_screen._memory.short_term.get_all.return_value = mock_messages
    mock_chat_screen._render_restored_history()
    # Should not render any messages
    mock_chat_screen._chat_area.add_message.assert_not_called()
    mock_chat_screen._chat_area.add_tool_call.assert_not_called()


def test_render_restored_history_only_system_messages(mock_chat_screen):
    """Test restoring history with only system messages."""
    mock_messages = [
        {"role": "system", "content": "[会话摘要] 这是一个测试摘要"},
        {"role": "system", "content": "[中断摘要] 用户中断了操作"},
    ]
    mock_chat_screen._memory = MagicMock()
    mock_chat_screen._memory.short_term.get_all.return_value = mock_messages
    mock_chat_screen._render_restored_history()
    # Should render system messages
    assert mock_chat_screen._chat_area.add_system.call_count >= 2


def test_render_restored_history_with_tool_messages(mock_chat_screen):
    """Test that tool messages are rendered when restoring history."""
    mock_messages = [
        {"role": "user", "content": "Search for something"},
        {"role": "assistant", "content": "I'll search for that"},
        {
            "role": "tool",
            "content": "Action: web_search[{\"query\": \"test\"}]\nObservation: [stdout]\nSearch results here",
        },
        {"role": "assistant", "content": "Here are the results"},
    ]

    mock_chat_screen._memory = MagicMock()
    mock_chat_screen._memory.short_term.get_all.return_value = mock_messages

    mock_chat_screen._render_restored_history()

    # Should call add_tool_call for tool messages
    mock_chat_screen._chat_area.add_tool_call.assert_called_once_with(
        "web_search", "[stdout]\nSearch results here"
    )
    # Should call add_message for user and assistant messages
    assert mock_chat_screen._chat_area.add_message.call_count == 3


def test_render_restored_history_multiple_tools(mock_chat_screen):
    """Test restoring history with multiple tool calls."""
    mock_messages = [
        {"role": "user", "content": "搜索并分析"},
        {
            "role": "tool",
            "content": "Action: web_search[{\"query\": \"test1\"}]\nObservation: 结果1",
        },
        {
            "role": "tool",
            "content": "Action: file_read[{\"path\": \"test.txt\"}]\nObservation: 文件内容",
        },
        {"role": "assistant", "content": "分析完成"},
    ]
    mock_chat_screen._memory = MagicMock()
    mock_chat_screen._memory.short_term.get_all.return_value = mock_messages
    mock_chat_screen._render_restored_history()
    # Should render both tool calls
    assert mock_chat_screen._chat_area.add_tool_call.call_count == 2


def test_render_restored_history_long_tool_result(mock_chat_screen):
    """Test that long tool results are truncated."""
    long_result = "x" * 1000
    mock_messages = [
        {
            "role": "tool",
            "content": f"Action: web_search[{{}}]\nObservation: {long_result}",
        },
    ]
    mock_chat_screen._memory = MagicMock()
    mock_chat_screen._memory.short_term.get_all.return_value = mock_messages
    mock_chat_screen._render_restored_history()
    # Should truncate to 500 chars
    call_args = mock_chat_screen._chat_area.add_tool_call.call_args
    assert len(call_args[0][1]) <= 500


def test_render_restored_history_with_cancel_summary(mock_chat_screen):
    """Test restoring history after user cancellation."""
    mock_messages = [
        {"role": "user", "content": "帮我搜索一些信息"},
        {"role": "assistant", "content": "正在搜索..."},
        {"role": "system", "content": "[中断摘要] 用户中断或取消信号"},
    ]
    mock_chat_screen._memory = MagicMock()
    mock_chat_screen._memory.short_term.get_all.return_value = mock_messages
    mock_chat_screen._render_restored_history()
    # Should render all messages including cancel summary
    assert mock_chat_screen._chat_area.add_system.call_count >= 1
    assert mock_chat_screen._chat_area.add_message.call_count >= 2


def test_render_restored_history_with_compact_summary(mock_chat_screen):
    """Test restoring history after compaction."""
    mock_messages = [
        {"role": "system", "content": "[会话摘要] 之前讨论了Python编程相关问题"},
        {"role": "user", "content": "继续之前的话题"},
        {"role": "assistant", "content": "好的，我们继续"},
    ]
    mock_chat_screen._memory = MagicMock()
    mock_chat_screen._memory.short_term.get_all.return_value = mock_messages
    mock_chat_screen._render_restored_history()
    # Should render compact summary as system message
    mock_chat_screen._chat_area.add_system.assert_any_call(
        "[会话摘要] 之前讨论了Python编程相关问题"
    )


def test_render_restored_history_with_snip_marker(mock_chat_screen):
    """Test restoring history after context snipping."""
    mock_messages = [
        {"role": "system", "content": "[上下文已裁剪] 此标记之前的对话历史已被移除，共移除 5 条消息。"},
        {"role": "user", "content": "新问题"},
        {"role": "assistant", "content": "新回答"},
    ]
    mock_chat_screen._memory = MagicMock()
    mock_chat_screen._memory.short_term.get_all.return_value = mock_messages
    mock_chat_screen._render_restored_history()
    # Should render snip marker
    mock_chat_screen._chat_area.add_system.assert_any_call(
        "[上下文已裁剪] 此标记之前的对话历史已被移除，共移除 5 条消息。"
    )


def test_render_restored_history_preserves_order(mock_chat_screen):
    """Test that message order is preserved after restoration."""
    mock_messages = [
        {"role": "user", "content": "第一个问题"},
        {"role": "assistant", "content": "第一个回答"},
        {"role": "user", "content": "第二个问题"},
        {"role": "assistant", "content": "第二个回答"},
    ]
    mock_chat_screen._memory = MagicMock()
    mock_chat_screen._memory.short_term.get_all.return_value = mock_messages
    mock_chat_screen._render_restored_history()
    # Should call add_message in order
    calls = mock_chat_screen._chat_area.add_message.call_args_list
    assert calls[0][0] == ("user", "第一个问题")
    assert calls[1][0] == ("assistant", "第一个回答")
    assert calls[2][0] == ("user", "第二个问题")
    assert calls[3][0] == ("assistant", "第二个回答")


def test_render_restored_history_mixed_roles(mock_chat_screen):
    """Test restoring history with all role types."""
    mock_messages = [
        {"role": "system", "content": "系统消息"},
        {"role": "user", "content": "用户消息"},
        {"role": "assistant", "content": "助手消息"},
        {
            "role": "tool",
            "content": "Action: test_tool[{\"arg\": \"value\"}]\nObservation: 工具结果",
        },
    ]
    mock_chat_screen._memory = MagicMock()
    mock_chat_screen._memory.short_term.get_all.return_value = mock_messages
    mock_chat_screen._render_restored_history()
    # Should render all role types
    mock_chat_screen._chat_area.add_system.assert_any_call("系统消息")
    mock_chat_screen._chat_area.add_message.assert_any_call("user", "用户消息")
    mock_chat_screen._chat_area.add_message.assert_any_call("assistant", "助手消息")
    mock_chat_screen._chat_area.add_tool_call.assert_called_once_with(
        "test_tool", "工具结果"
    )


def test_render_restored_history_tool_without_bracket(mock_chat_screen):
    """Test restoring tool message without bracket format."""
    mock_messages = [
        {
            "role": "tool",
            "content": "Some raw tool output without Action: prefix",
        },
    ]
    mock_chat_screen._memory = MagicMock()
    mock_chat_screen._memory.short_term.get_all.return_value = mock_messages
    mock_chat_screen._render_restored_history()
    # Should use "tool" as default name
    mock_chat_screen._chat_area.add_tool_call.assert_called_once_with(
        "tool", "Some raw tool output without Action: prefix"
    )


def test_render_restored_history_restored_count_message(mock_chat_screen):
    """Test that restored count message is displayed."""
    mock_messages = [
        {"role": "user", "content": "问题"},
        {"role": "assistant", "content": "回答"},
    ]
    mock_chat_screen._memory = MagicMock()
    mock_chat_screen._memory.short_term.get_all.return_value = mock_messages
    mock_chat_screen._render_restored_history()
    # Should show restored count
    mock_chat_screen._chat_area.add_system.assert_any_call(
        "[dim]Restored 2 messages from this session.[/]"
    )
