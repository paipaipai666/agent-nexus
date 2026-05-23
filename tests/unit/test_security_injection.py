"""Security boundary tests for AgentNexus tool system —
injection, path traversal, and edge-case input validation."""

from unittest.mock import MagicMock, patch

import pytest

from agentnexus.tools.file_ops import _resolve_safe, file_read
from agentnexus.tools.memory_save import memory_save
from agentnexus.tools.shell import _check_blacklist, shell_exec


class TestShellInjection:
    """shell_exec / _check_blacklist with injection-like command strings."""

    @patch("agentnexus.tools.shell._SYSTEM", "Linux")
    def test_command_injection_semicolon(self):
        """Semicolon-chained destructive command caught by blacklist."""
        result = _check_blacklist("echo hello; rm -rf /")
        assert result is not None
        assert "blocked" in result

    @patch("agentnexus.tools.shell._SYSTEM", "Linux")
    def test_command_injection_pipe(self):
        """Pipe-chained destructive command caught by blacklist."""
        result = _check_blacklist("echo hello | rm -rf /")
        assert result is not None
        assert "blocked" in result

    @patch("agentnexus.tools.shell._SYSTEM", "Linux")
    def test_command_injection_subshell(self):
        """Subshell syntax does not crash blacklist checker."""
        result = _check_blacklist("echo $(cat /etc/passwd)")
        assert result is None

    def test_backtick_subshell(self):
        """Backtick subshell does not crash blacklist checker."""
        result = _check_blacklist("echo `cat /etc/passwd`")
        assert result is None

    @patch("agentnexus.tools.shell.get_settings")
    @patch("agentnexus.tools.shell._check_blacklist", return_value="[blocked] blocked")
    def test_empty_command(self, mock_check, mock_settings):
        """Empty command string is safely handled by shell_exec."""
        mock_settings.return_value.shell_enabled = True
        result = shell_exec("")
        assert "blocked" in result

    @patch("agentnexus.tools.shell.get_settings")
    @patch("agentnexus.tools.shell._check_blacklist")
    def test_shell_exec_handles_blocked(self, mock_check, mock_settings):
        """shell_exec returns blocked message without crashing."""
        mock_settings.return_value.shell_enabled = True
        mock_check.return_value = "[blocked] 命令已被安全策略拦截"
        result = shell_exec("echo hello; rm -rf /")
        assert "blocked" in result or "拦截" in result

    @patch("agentnexus.tools.shell.get_settings")
    @patch("agentnexus.tools.shell._check_blacklist")
    def test_shell_exec_handles_pipe_blocked(self, mock_check, mock_settings):
        """shell_exec blocks piped destructive command."""
        mock_settings.return_value.shell_enabled = True
        mock_check.return_value = "[blocked] blocked"
        result = shell_exec("echo hello | rm -rf /")
        assert "blocked" in result

    @patch("agentnexus.tools.shell.get_settings")
    @patch("agentnexus.tools.shell._check_blacklist")
    def test_shell_exec_handles_subshell(self, mock_check, mock_settings):
        """shell_exec with subshell syntax does not crash."""
        mock_settings.return_value.shell_enabled = True
        mock_check.return_value = None
        with patch("agentnexus.tools.shell.subprocess.run") as mock_run:
            mock_run.return_value.stdout = ""
            mock_run.return_value.stderr = ""
            mock_run.return_value.returncode = 0
            result = shell_exec("echo $(whoami)")
            assert "exit_code" in result


class TestPathTraversal:
    """_resolve_safe / file_read with path-traversal vectors."""

    @patch("agentnexus.tools.file_ops.os.getcwd", return_value="D:\\code\\AgentNexus")
    def test_relative_escape(self, mock_getcwd):
        """../../../etc/passwd raises ValueError."""
        with pytest.raises(ValueError, match="路径越界|out of bounds"):
            _resolve_safe("../../../etc/passwd")

    @patch("agentnexus.tools.file_ops.os.getcwd", return_value="D:\\code\\AgentNexus")
    def test_absolute_outside(self, mock_getcwd):
        """Absolute path on different drive / outside workspace raises."""
        with pytest.raises(ValueError, match="路径越界|out of bounds"):
            _resolve_safe("C:\\Windows\\system32")

    @patch("agentnexus.tools.file_ops.os.getcwd", return_value="D:\\code\\AgentNexus")
    def test_dot_dot_escape(self, mock_getcwd):
        """foo/../../bar escapes workspace (two levels up from workspace root)."""
        with pytest.raises(ValueError, match="路径越界|out of bounds"):
            _resolve_safe("foo/../../bar")

    @patch("agentnexus.tools.file_ops.os.getcwd", return_value="D:\\code\\AgentNexus")
    def test_normal_path(self, mock_getcwd):
        """Valid relative path resolves without error."""
        p = _resolve_safe(".")
        assert p is not None

    @patch("agentnexus.tools.file_ops.os.getcwd", return_value="D:\\code\\AgentNexus")
    def test_file_read_relative_escape_raises(self, mock_getcwd):
        """file_read with traversal raises ValueError."""
        with pytest.raises(ValueError, match="路径越界|out of bounds"):
            file_read("../../../etc/passwd")

    @patch("agentnexus.tools.file_ops.os.getcwd", return_value="D:\\code\\AgentNexus")
    def test_file_read_absolute_outside_raises(self, mock_getcwd):
        """file_read with absolute path outside workspace raises ValueError."""
        with pytest.raises(ValueError, match="路径越界|out of bounds"):
            file_read("C:\\Windows\\system32")

    def test_file_read_normal_path_not_found(self):
        """file_read with valid relative path returns file-not-found, not security error."""
        result = file_read("nonexistent_file_xyz_123.txt")
        assert "文件不存在" in result


class TestMemorySave:
    """memory_save with edge-case content (HTML, long, unicode)."""

    @patch("agentnexus.tools.memory_save.get_long_term_memory")
    @patch("agentnexus.tools.memory_save.get_embedding_model")
    def test_html_injection(self, mock_get_emb, mock_get_ltm):
        """HTML/script content saved as-is — no injection vector since it's text."""
        mock_model = MagicMock()
        mock_model.encode.return_value.tolist.return_value = [0.1]
        mock_get_emb.return_value = mock_model
        result = memory_save("<script>alert('xss')</script>", category="entity_fact")
        assert "已保存" in result

    @patch("agentnexus.tools.memory_save.get_long_term_memory")
    @patch("agentnexus.tools.memory_save.get_embedding_model")
    def test_long_string(self, mock_get_emb, mock_get_ltm):
        """Very long content is truncated in response message but saved in full."""
        mock_model = MagicMock()
        mock_model.encode.return_value.tolist.return_value = [0.1]
        mock_get_emb.return_value = mock_model
        long_content = "A" * 10000
        result = memory_save(long_content, category="entity_fact")
        assert "已保存" in result
        _, kwargs = mock_get_ltm.return_value.save.call_args
        assert len(kwargs["content"]) == 10000

    @patch("agentnexus.tools.memory_save.get_long_term_memory")
    @patch("agentnexus.tools.memory_save.get_embedding_model")
    def test_unicode_injection(self, mock_get_emb, mock_get_ltm):
        """Unicode special characters saved correctly."""
        mock_model = MagicMock()
        mock_model.encode.return_value.tolist.return_value = [0.1]
        mock_get_emb.return_value = mock_model
        result = memory_save("普通文本 + unicode: \u0000\u00ff\u4e2d\u6587", category="entity_fact")
        assert "已保存" in result

    @patch("agentnexus.tools.memory_save.get_long_term_memory")
    @patch("agentnexus.tools.memory_save.get_embedding_model")
    def test_html_injection_all_categories(self, mock_get_emb, mock_get_ltm):
        """HTML content saved under all valid categories."""
        mock_model = MagicMock()
        mock_model.encode.return_value.tolist.return_value = [0.1]
        mock_get_emb.return_value = mock_model
        for cat in ["user_preference", "entity_fact", "conclusion"]:
            result = memory_save(f"<b>bold {cat}</b>", category=cat)
            assert "已保存" in result
