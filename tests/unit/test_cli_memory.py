from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from agentnexus.cli import app

runner = CliRunner()

class TestMemoryList:
    def test_memory_list_empty(self):
        """When no memories, show 暂无记忆"""
        mock_ltm = MagicMock()
        mock_ltm.list_recent.return_value = []
        with patch("agentnexus.cli.memory_cmd.get_long_term_memory", return_value=mock_ltm):
            result = runner.invoke(app, ["memory", "list"])
            assert "暂无记忆" in result.stdout
            assert result.exit_code == 0

    def test_memory_list_with_data(self):
        """Show memories in a table"""
        mock_ltm = MagicMock()
        mock_ltm.list_recent.return_value = [
            {"id": 1, "category": "fact", "importance": 3.5, "content": "test memory"},
        ]
        with patch("agentnexus.cli.memory_cmd.get_long_term_memory", return_value=mock_ltm):
            result = runner.invoke(app, ["memory", "list"])
            assert "test memory" in result.stdout
            assert "fact" in result.stdout
            assert "3.5" in result.stdout
            assert result.exit_code == 0

    def test_memory_list_limit(self):
        """Custom limit is passed to list_recent"""
        mock_ltm = MagicMock()
        mock_ltm.list_recent.return_value = []
        with patch("agentnexus.cli.memory_cmd.get_long_term_memory", return_value=mock_ltm):
            runner.invoke(app, ["memory", "list", "--limit", "5"])
            mock_ltm.list_recent.assert_called_once_with(5)

class TestMemoryClear:
    def test_memory_clear(self):
        """Clears all memories"""
        mock_ltm = MagicMock()
        with patch("agentnexus.cli.memory_cmd.get_long_term_memory", return_value=mock_ltm):
            result = runner.invoke(app, ["memory", "clear"])
            mock_ltm.clear_all.assert_called_once()
            assert "记忆已清空" in result.stdout
            assert result.exit_code == 0
