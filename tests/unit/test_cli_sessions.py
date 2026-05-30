"""Tests for nexus sessions CLI command."""

import os

import pytest
from typer.testing import CliRunner

from agentnexus.cli import app

runner = CliRunner()


@pytest.fixture
def setup_sessions(temp_agentnexus_home):
    """Create test sessions in the database."""
    from agentnexus.core.config import get_settings
    from agentnexus.memory.short_term import ShortTermMemory
    from agentnexus.memory.versioned import ConversationVersionManager

    settings = get_settings()
    workspace = str(temp_agentnexus_home)

    # Create sessions with checkpoints
    for i in range(3):
        mgr = ConversationVersionManager(
            f"session_{i}", settings.memory_db_path, workspace_path=workspace
        )
        stm = ShortTermMemory()
        stm.append("user", f"Question {i}")
        stm.append("assistant", f"Answer {i}")
        mgr.commit(stm.to_json(), question=f"Question {i}", answer=f"Answer {i}")
        mgr._conn.close()

    return workspace


def test_sessions_command_no_sessions():
    """Test sessions command when no sessions exist."""
    with runner.isolated_filesystem():
        result = runner.invoke(app, ["sessions"])
        assert result.exit_code == 0
        assert "No previous sessions found" in result.output


def test_sessions_command_with_sessions(setup_sessions):
    """Test sessions command with existing sessions."""
    os.chdir(setup_sessions)
    result = runner.invoke(app, ["sessions"])
    assert result.exit_code == 0
    assert "session_0" in result.output
    assert "session_1" in result.output
    assert "session_2" in result.output


def test_sessions_command_with_limit(setup_sessions):
    """Test sessions command with --limit option."""
    os.chdir(setup_sessions)
    result = runner.invoke(app, ["sessions", "--limit", "2"])
    assert result.exit_code == 0
    # Should only show 2 sessions
    assert "session_2" in result.output
    assert "session_1" in result.output
