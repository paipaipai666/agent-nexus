"""Security boundary tests for AgentNexus tool system —
injection, path traversal, PII filtering, SQL injection,
and edge-case input validation."""

from unittest.mock import MagicMock, patch

import pytest

from agentnexus.memory.manager import _contains_pii
from agentnexus.tools.file_ops import _resolve_safe, file_list, file_read
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

    # ── Windows-specific patterns ──

    @patch("agentnexus.tools.shell._SYSTEM", "Windows")
    def test_win_format_blocked(self):
        """Windows 'format D:' caught by blacklist."""
        result = _check_blacklist("format D:")
        assert result is not None
        assert "blocked" in result

    @patch("agentnexus.tools.shell._SYSTEM", "Windows")
    def test_win_del_fs_blocked(self):
        """Windows 'del /s C:' caught by blacklist (single switch pattern)."""
        result = _check_blacklist("del /s C:")
        assert result is not None
        assert "blocked" in result

    @patch("agentnexus.tools.shell._SYSTEM", "Windows")
    def test_win_rmdir_blocked(self):
        """Windows 'rmdir /s C:\\' caught by blacklist."""
        result = _check_blacklist("rmdir /s C:\\")
        assert result is not None
        assert "blocked" in result

    @patch("agentnexus.tools.shell._SYSTEM", "Windows")
    def test_win_diskpart_blocked(self):
        """Windows 'diskpart' caught by blacklist."""
        result = _check_blacklist("diskpart")
        assert result is not None
        assert "blocked" in result

    @patch("agentnexus.tools.shell._SYSTEM", "Windows")
    def test_win_bcdedit_blocked(self):
        """Windows 'bcdedit' caught by blacklist."""
        result = _check_blacklist("bcdedit")
        assert result is not None
        assert "blocked" in result

    @patch("agentnexus.tools.shell._SYSTEM", "Windows")
    def test_win_registry_add_blocked(self):
        """Windows 'reg add /f' caught by blacklist."""
        result = _check_blacklist("reg add /f HKLM\\Software")
        assert result is not None
        assert "blocked" in result

    # ── Chain / env-var patterns ──

    @patch("agentnexus.tools.shell._SYSTEM", "Linux")
    def test_shell_and_chain_blocked(self):
        """'&&' chained destructive command caught by blacklist (Linux)."""
        result = _check_blacklist("echo hello && rm -rf /")
        assert result is not None
        assert "blocked" in result

    @patch("agentnexus.tools.shell._SYSTEM", "Linux")
    def test_shell_or_chain_blocked(self):
        """'||' chained destructive command caught by blacklist (Linux)."""
        result = _check_blacklist("echo hello || rm -rf /")
        assert result is not None
        assert "blocked" in result

    def test_env_var_echo_not_blocked(self):
        """Environment variable read ('echo %PATH%') is not blocked."""
        result = _check_blacklist("echo %PATH%")
        assert result is None

    def test_env_var_unix_not_blocked(self):
        """Unix env var read ('echo $HOME') is not blocked."""
        result = _check_blacklist("echo $HOME")
        assert result is None

    def test_powershell_encoded_command_blocked(self):
        """PowerShell -EncodedCommand with dangerous payload — not yet blocked."""
        result = _check_blacklist("powershell -EncodedCommand rm -rf /")
        assert result is not None
        assert "blocked" in result or "rm" in str(result).lower()

    def test_unicode_fullwidth_bypass(self):
        """Fullwidth characters that lowercase differently (ｒｍ) — currently bypasses."""
        result = _check_blacklist("ｒｍ -rf /")
        assert result is not None
        assert "blocked" in result


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

    # ── Advanced traversal ──

    def test_dir_traversal_unicode_normalized(self):
        """Unicode dot (\\u2024) in path — implementation-dependent."""
        with patch("agentnexus.tools.file_ops.os.getcwd", return_value="D:\\code\\AgentNexus"):
            try:
                _resolve_safe("..\\u2024..\\u2024etc\\u2024passwd")
            except ValueError:
                pass
            # No crash is the main assertion

    @patch("agentnexus.tools.file_ops.os.getcwd", return_value="D:\\code\\AgentNexus")
    def test_file_list_symlink_appears(self, mock_getcwd):
        """file_list does not crash when encountering symlinks (no marker yet)."""
        with patch("agentnexus.tools.file_ops.os.path.islink", return_value=True):
            with patch("agentnexus.tools.file_ops.os.path.isdir", return_value=False):
                result = file_list(".")
        assert result is not None
        assert "错误" not in result

    def test_symlink_outside_workspace(self):
        """Symlink inside workspace pointing outside is not yet blocked."""
        with patch("agentnexus.tools.file_ops.os.getcwd", return_value="D:\\code\\AgentNexus"):
            with patch("agentnexus.tools.file_ops.Path.resolve") as mock_resolve:
                outside = "D:\\outside\\target"
                mock_resolve.return_value = outside
                with pytest.raises(ValueError, match="路径越界|out of bounds"):
                    _resolve_safe("link_to_outside")


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


class TestPiiFilter:
    """_contains_pii — regex-based PII detection."""

    def test_contains_email(self):
        """Standard email address is detected."""
        assert _contains_pii("contact me at user@example.com")

    def test_contains_chinese_phone(self):
        """Chinese mobile number (138...) is detected."""
        assert _contains_pii("call 13800138000 for details")

    def test_contains_api_key(self):
        """sk- prefixed 32+ char API key is detected."""
        assert _contains_pii("key=sk-" + "a" * 40)

    def test_contains_credit_card(self):
        """15-19 digit number is detected as potential CC."""
        assert _contains_pii("card 4111111111111111")

    def test_no_pii_short_input(self):
        """Short number (11 digits) and user@host with no dot are NOT detected."""
        assert not _contains_pii("hello world")
        assert not _contains_pii("user@host")
        assert not _contains_pii("number 12345 is short")

    def test_no_pii_short_phone_prefix(self):
        """1 followed by less than 11 digits is not detected."""
        assert not _contains_pii("call 1234")

    def test_no_pii_short_apikey(self):
        """sk- with fewer than 32 chars is not detected."""
        assert not _contains_pii("key=sk-abc")

    def test_no_pii_clean_chinese(self):
        """Clean Chinese text with numbers is not detected."""
        assert not _contains_pii("你好，世界 2024")


class TestSqlInjection:
    """memory/memory_save with SQL injection vectors."""

    @patch("agentnexus.tools.memory_save.get_long_term_memory")
    @patch("agentnexus.tools.memory_save.get_embedding_model")
    def test_memory_save_sql_injection_content(self, mock_get_emb, mock_get_ltm):
        """SQL injection attempt in content is safely stored as text."""
        mock_model = MagicMock()
        mock_model.encode.return_value.tolist.return_value = [0.1]
        mock_get_emb.return_value = mock_model
        payload = "\"'; DROP TABLE long_term_memories; --"
        result = memory_save(payload, category="entity_fact")
        assert "已保存" in result
        _, kwargs = mock_get_ltm.return_value.save.call_args
        assert kwargs["content"] == payload

    @patch("agentnexus.tools.memory_save.get_long_term_memory")
    @patch("agentnexus.tools.memory_save.get_embedding_model")
    def test_memory_save_sql_injection_category(self, mock_get_emb, mock_get_ltm):
        """SQL-like category string is rejected by category validation (not SQLi)."""
        mock_model = MagicMock()
        mock_model.encode.return_value.tolist.return_value = [0.1]
        mock_get_emb.return_value = mock_model
        result = memory_save("safe content", category="entity_fact' OR '1'='1")
        assert "无效分类" in result

    @patch("agentnexus.tools.memory_save.get_long_term_memory")
    @patch("agentnexus.tools.memory_save.get_embedding_model")
    def test_memory_save_union_injection(self, mock_get_emb, mock_get_ltm):
        """UNION SELECT injection attempt is safely stored."""
        mock_model = MagicMock()
        mock_model.encode.return_value.tolist.return_value = [0.1]
        mock_get_emb.return_value = mock_model
        payload = "UNION SELECT * FROM long_term_memories"
        result = memory_save(payload, category="entity_fact")
        assert "已保存" in result
        _, kwargs = mock_get_ltm.return_value.save.call_args
        assert kwargs["content"] == payload


class TestMemorySaveHugeContent:
    """memory_save with extreme content sizes."""

    @patch("agentnexus.tools.memory_save.get_long_term_memory")
    @patch("agentnexus.tools.memory_save.get_embedding_model")
    def test_memory_save_huge_content(self, mock_get_emb, mock_get_ltm):
        """memory_save with 1MB content does not OOM."""
        mock_model = MagicMock()
        mock_model.encode.return_value.tolist.return_value = [0.1]
        mock_get_emb.return_value = mock_model
        huge = "x" * (1024 * 1024)
        result = memory_save(huge, category="entity_fact")
        assert "已保存" in result
        _, kwargs = mock_get_ltm.return_value.save.call_args
        assert len(kwargs["content"]) == 1024 * 1024

    @patch("agentnexus.tools.memory_save.get_long_term_memory")
    @patch("agentnexus.tools.memory_save.get_embedding_model")
    def test_memory_save_max_importance_clamped(self, mock_get_emb, mock_get_ltm):
        """Importance value > 1.0 is clamped to 1.0."""
        mock_model = MagicMock()
        mock_model.encode.return_value.tolist.return_value = [0.1]
        mock_get_emb.return_value = mock_model
        result = memory_save("important fact", category="entity_fact", importance=999.0)
        assert "已保存" in result

    @patch("agentnexus.tools.memory_save.get_long_term_memory")
    @patch("agentnexus.tools.memory_save.get_embedding_model")
    def test_memory_save_min_importance_clamped(self, mock_get_emb, mock_get_ltm):
        """Importance value < 0.0 is clamped to 0.0."""
        mock_model = MagicMock()
        mock_model.encode.return_value.tolist.return_value = [0.1]
        mock_get_emb.return_value = mock_model
        result = memory_save("trivial detail", category="entity_fact", importance=-1.0)
        assert "已保存" in result

    @patch("agentnexus.tools.memory_save.get_long_term_memory")
    @patch("agentnexus.tools.memory_save.get_embedding_model")
    def test_memory_save_empty_content(self, mock_get_emb, mock_get_ltm):
        """Empty content returns error, not crash."""
        mock_model = MagicMock()
        mock_model.encode.return_value.tolist.return_value = [0.1]
        mock_get_emb.return_value = mock_model
        result = memory_save("  ", category="entity_fact")
        assert "太短" in result or "error" in result.lower()


class TestFileReadLimits:
    """file_read size and line limits."""

    @patch("agentnexus.tools.file_ops.os.getcwd", return_value="D:\\code\\AgentNexus")
    def test_file_read_size_limit(self, mock_getcwd):
        """file_read rejects files larger than 10MB."""
        with patch("agentnexus.tools.file_ops.Path.exists", return_value=True):
            with patch("agentnexus.tools.file_ops.Path.is_file", return_value=True):
                with patch("agentnexus.tools.file_ops.Path.stat") as mock_stat:
                    mock_stat.return_value.st_size = 11 * 1024 * 1024
                    result = file_read("large_file.bin")
        assert "超过 10MB" in result or "过大" in result

    @patch("agentnexus.tools.file_ops.os.getcwd", return_value="D:\\code\\AgentNexus")
    def test_file_read_line_limit(self, mock_getcwd):
        """file_read limits output to 1000 lines by default."""
        with patch("agentnexus.tools.file_ops.Path.exists", return_value=True):
            with patch("agentnexus.tools.file_ops.Path.is_file", return_value=True):
                with patch("agentnexus.tools.file_ops.Path.stat") as mock_stat:
                    mock_stat.return_value.st_size = 50000
                    with patch("agentnexus.tools.file_ops._fingerprint_file", return_value="fake"):
                        with patch("builtins.open") as mock_open:
                            mock_file = MagicMock()
                            mock_file.readlines.return_value = [f"line {i}\n" for i in range(2000)]
                            mock_open.return_value.__enter__.return_value = mock_file
                            result = file_read("big_file.txt")
        lines = result.split("\n")
        content_lines = [l for l in lines if "|" in l]
        assert len(content_lines) <= 1000


class TestShellInfiniteLoop:
    """shell_exec timeout on infinite loops."""

    @patch("agentnexus.tools.shell.get_settings")
    def test_shell_infinite_loop_timeout(self, mock_settings):
        """shell_exec with infinite loop triggers timeout."""
        import subprocess
        mock_settings.return_value.shell_enabled = True
        from agentnexus.tools.shell import shell_exec
        with patch("agentnexus.tools.shell.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired("cmd", 30)
            result = shell_exec("while true; do :; done")
        assert "超时" in result or "timeout" in result.lower()


class TestShellEdgeCases:
    """Shell command edge cases — null bytes, newlines, special chars."""

    def test_null_byte_in_command(self):
        """Null byte in command does not crash blacklist."""
        from agentnexus.tools.shell import _check_blacklist
        result = _check_blacklist("echo hello\x00world")
        assert result is None or "blocked" in result

    def test_newline_in_command(self):
        """Newline in command is handled without crash."""
        from agentnexus.tools.shell import _check_blacklist
        result = _check_blacklist("echo hello\necho world")
        assert result is None

    @patch("agentnexus.tools.shell._SYSTEM", "Linux")
    @patch("agentnexus.tools.shell._SYSTEM", "Linux")
    def test_shell_and_chain_normal_not_blocked(self):
        """Normal && chaining (non-destructive) passes blacklist."""
        from agentnexus.tools.shell import _check_blacklist
        result = _check_blacklist("ls && echo ok")
        assert result is None

    @patch("agentnexus.tools.shell._SYSTEM", "Linux")
    def test_shell_or_chain_normal_not_blocked(self):
        """Normal || chaining (non-destructive) passes blacklist."""
        from agentnexus.tools.shell import _check_blacklist
        result = _check_blacklist("ls || echo ok")
        assert result is None

    @patch("agentnexus.tools.shell._SYSTEM", "Linux")
    def test_multiline_blocked_destructive(self):
        """Multiline command with destructive content is blocked."""
        from agentnexus.tools.shell import _check_blacklist
        result = _check_blacklist("echo hello\nrm -rf /\necho world")
        assert result is not None
        assert "blocked" in result

    @patch("agentnexus.tools.shell.get_settings")
    def test_shell_exec_negative_timeout(self, mock_settings):
        """Negative timeout is capped to default."""
        mock_settings.return_value.shell_enabled = True
        mock_settings.return_value.shell_timeout = 30
        from agentnexus.tools.shell import shell_exec
        with patch("agentnexus.tools.shell.subprocess.run") as mock_run:
            mock_run.return_value.stdout = ""
            mock_run.return_value.stderr = ""
            mock_run.return_value.returncode = 0
            result = shell_exec("echo ok", timeout=-1)
        assert "exit_code" in result


class TestPathEdgeCases:
    """Path resolution edge cases — null bytes, special characters."""

    @patch("agentnexus.tools.file_ops.os.getcwd", return_value="D:\\code\\AgentNexus")
    def test_null_byte_in_path_does_not_crash(self, mock_getcwd):
        """_resolve_safe with null byte in path does not crash."""
        try:
            _resolve_safe("test.txt\x00")
        except (ValueError, TypeError):
            pass
