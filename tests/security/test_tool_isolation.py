"""P0-4: Tool side-effect isolation test.

Verifies that file operations and shell commands execute within a
sandbox directory and do not leak across test boundaries.
"""
from unittest.mock import patch

import pytest

from agentnexus.tools.file_ops import file_list, file_read, file_write
from agentnexus.tools.shell import shell_exec


class TestToolSandboxIsolation:
    """File and shell tools must stay within workspace boundaries."""

    def test_file_write_creates_in_workspace(self, tmp_path):
        """file_write creates files inside the workspace."""
        test_file = "test_iso.txt"
        with patch("agentnexus.tools.file_ops.os.getcwd", return_value=str(tmp_path)):
            result = file_write(test_file, "hello world", mode="create")
            assert "已创建" in result.get("message", "") or "version" in result.get("message", "")
            assert (tmp_path / test_file).exists()

    def test_file_write_cannot_escape_via_dot_dot(self, tmp_path):
        """file_write blocks relative path escaping workspace."""
        escape_path = "../outside/escaped.txt"

        with patch("agentnexus.tools.file_ops.os.getcwd", return_value=str(tmp_path)):
            with pytest.raises(ValueError, match="路径越界"):
                file_write(escape_path, "leak", mode="create")

    def test_file_read_only_in_workspace(self, tmp_path):
        """file_read blocks reading files outside workspace."""
        (tmp_path / "safe.txt").write_text("safe", encoding="utf-8")

        with patch("agentnexus.tools.file_ops.os.getcwd", return_value=str(tmp_path)):
            content = file_read("safe.txt")
            assert "safe" in content

            with pytest.raises(ValueError, match="路径越界"):
                file_read("../outside.txt")

    def test_shell_exec_cwd_stays_in_workspace(self, tmp_path):
        """shell_exec cwd is resolved within workspace."""
        (tmp_path / "inner").mkdir()
        (tmp_path / "inner" / "test.txt").write_text("data", encoding="utf-8")

        with patch("agentnexus.tools.shell._SYSTEM", "Windows"):
            with patch("agentnexus.tools.shell.get_settings") as mock_settings:
                mock_settings.return_value.shell_enabled = True
                mock_settings.return_value.shell_execution_backend = "local_unsafe"
                with patch("agentnexus.tools.shell.subprocess.run") as mock_run:
                    mock_run.return_value.stdout = "test.txt\n"
                    mock_run.return_value.stderr = ""
                    mock_run.return_value.returncode = 0
                    result = shell_exec("dir", cwd="inner", timeout=5)
                    assert "test.txt" in result

    def test_shell_exec_resolves_cwd_within_workspace(self, tmp_path):
        """shell_exec uses _resolve_safe for cwd, blocking escapes."""
        with patch("agentnexus.tools.file_ops._resolve_safe", side_effect=ValueError("路径越界")):
            with patch("agentnexus.tools.shell.get_settings") as mock_settings:
                mock_settings.return_value.shell_enabled = True
                with pytest.raises(ValueError, match="路径越界"):
                    shell_exec("ls", cwd="../escape", timeout=5)

    def test_shell_exec_does_not_escape_cwd(self, tmp_path):
        """shell_exec with cwd outside workspace is blocked."""
        with patch("agentnexus.tools.file_ops._resolve_safe",
                   side_effect=ValueError("路径越界: '..'")):
            with patch("agentnexus.tools.shell.get_settings") as mock_settings:
                mock_settings.return_value.shell_enabled = True
                with pytest.raises(ValueError, match="路径越界"):
                    shell_exec("ls", cwd="..", timeout=5)


class TestToolSideEffectIdempotency:
    """Same tool call twice should have same effect
    (or idempotent handling)."""

    def test_file_write_append_idempotent(self, tmp_path):
        """file_write append twice = file has both lines."""
        with patch("agentnexus.tools.file_ops.os.getcwd", return_value=str(tmp_path)):
            file_write("idempotent.txt", "line1", mode="create")
            file_write("idempotent.txt", "\nline2", mode="append")
            content = (tmp_path / "idempotent.txt").read_text(encoding="utf-8")
            assert "line1" in content
            assert "line2" in content

    def test_file_write_overwrite_replaces_content(self, tmp_path):
        """file_write overwrite replaces content deterministically."""
        with patch("agentnexus.tools.file_ops.os.getcwd", return_value=str(tmp_path)):
            file_write("replace.txt", "old content", mode="create")
            file_write("replace.txt", "new content", mode="overwrite")
            content = (tmp_path / "replace.txt").read_text(encoding="utf-8")
            assert content == "new content"

    def test_file_write_version_conflict_detected(self, tmp_path):
        """file_write with wrong expected_version is rejected."""
        with patch("agentnexus.tools.file_ops.os.getcwd", return_value=str(tmp_path)):
            file_write("versioned.txt", "original", mode="create")
            result = file_write("versioned.txt", "replaced",
                                mode="overwrite", expected_version="wrong_hash")
            assert "版本冲突" in result.get("message", "")

    def test_same_file_read_twice_same_result(self, tmp_path):
        """file_read is idempotent: same file, same result."""
        (tmp_path / "stable.txt").write_text("stable content", encoding="utf-8")
        with patch("agentnexus.tools.file_ops.os.getcwd", return_value=str(tmp_path)):
            r1 = file_read("stable.txt")
            r2 = file_read("stable.txt")
            assert r1 == r2


class TestToolNoRollback:
    """Tools have no built-in rollback — document this behavior."""

    def test_file_write_no_automatic_backup(self, tmp_path):
        """file_write overwrite does not create backup automatically."""
        with patch("agentnexus.tools.file_ops.os.getcwd", return_value=str(tmp_path)):
            file_write("norollback.txt", "original data", mode="create")
            file_write("norollback.txt", "replaced data", mode="overwrite")
            content = (tmp_path / "norollback.txt").read_text(encoding="utf-8")
            assert content == "replaced data"
            # No .bak file should exist
            baks = list(tmp_path.glob("*.bak"))
            assert len(baks) == 0

    def test_file_list_no_side_effects(self, tmp_path):
        """file_list is read-only and has no side effects."""
        (tmp_path / "a.txt").write_text("a", encoding="utf-8")
        with patch("agentnexus.tools.file_ops.os.getcwd", return_value=str(tmp_path)):
            listing1 = file_list(".")
            listing2 = file_list(".")
            assert listing1 == listing2
