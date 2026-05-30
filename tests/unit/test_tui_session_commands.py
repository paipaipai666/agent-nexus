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
