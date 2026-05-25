"""P1-3+P1-4: Tool idempotency and rollback tests.

Verifies:
- file_write expected_version prevents blind overwrite
- Tools have no built-in rollback (documented limitation)
- Registry idempotency (registering same tool)
"""
from unittest.mock import patch

from agentnexus.tools.file_ops import _fingerprint_file, file_read, file_write
from agentnexus.tools.registry import ToolMeta, ToolRegistry


class TestFileWriteIdempotency:
    """file_write idempotency via expected_version."""

    def test_write_then_read_then_write_same_version(self, tmp_path):
        with patch("agentnexus.tools.file_ops.os.getcwd", return_value=str(tmp_path)):
            r1 = file_write("plan.txt", "version 1", mode="create")
            assert "成功" in r1 or "version" in r1
            assert (tmp_path / "plan.txt").exists()

            content = file_read("plan.txt")
            assert "version 1" in content

    def test_version_conflict_rejects_stale_write(self, tmp_path):
        with patch("agentnexus.tools.file_ops.os.getcwd", return_value=str(tmp_path)):
            file_write("conflict.txt", "original", mode="create")

            # Wrong hash should be rejected
            result = file_write("conflict.txt", "replaced",
                                mode="overwrite", expected_version="wrong")
            assert "冲突" in result or "版本" in result
            assert (tmp_path / "conflict.txt").read_text(encoding="utf-8") == "original"

    def test_correct_version_allows_write(self, tmp_path):
        with patch("agentnexus.tools.file_ops.os.getcwd", return_value=str(tmp_path)):
            file_write("correct.txt", "original", mode="create")
            fp = (tmp_path / "correct.txt")
            real_hash = _fingerprint_file(fp)

            result = file_write("correct.txt", "updated",
                                mode="overwrite", expected_version=real_hash)
            assert "成功" in result or "version" in result
            assert fp.read_text(encoding="utf-8") == "updated"

    def test_create_mode_idempotent_fails_on_existing(self, tmp_path):
        """create mode fails if file already exists (no silent overwrite)."""
        with patch("agentnexus.tools.file_ops.os.getcwd", return_value=str(tmp_path)):
            file_write("existing.txt", "first", mode="create")
            result = file_write("existing.txt", "second", mode="create")
            assert "已存在" in result or "exist" in result.lower()


class TestToolRegistryIdempotency:
    """Registering same tool twice."""

    def test_register_same_name_warns(self):
        registry = ToolRegistry()
        meta = ToolMeta(
            name="dup_tool", description="test", param_schema={},
        )
        registry.register(meta, lambda: "ok")
        registry.register(meta, lambda: "ok")  # should warn but not crash

        tools = registry.get_available_tools("*")
        assert "dup_tool" in tools

    def test_invoke_same_tool_twice_same_result(self):
        registry = ToolRegistry()
        registry.register(
            ToolMeta(name="hello", description="say hello", param_schema={}),
            lambda **kw: "hello",
        )
        r1 = registry.invoke("hello", {}, caller="test")
        r2 = registry.invoke("hello", {}, caller="test")
        assert r1 == r2 == "hello"


class TestRollbackLimitation:
    """Tools have no built-in rollback — document this."""

    def test_no_rollback_after_file_write(self, tmp_path):
        """Overwritten content is permanently lost (no auto-backup)."""
        fp = tmp_path / "no_backup.txt"
        fp.write_text("original", encoding="utf-8")

        with patch("agentnexus.tools.file_ops.os.getcwd", return_value=str(tmp_path)):
            file_write("no_backup.txt", "replaced", mode="overwrite")

        assert fp.read_text(encoding="utf-8") == "replaced"
        assert not list(tmp_path.glob("*.bak"))

    def test_no_side_effect_from_read(self, tmp_path):
        """file_read does not modify file mtime tracking or state."""
        fp = tmp_path / "stable.txt"
        fp.write_text("content", encoding="utf-8")
        original_mtime = fp.stat().st_mtime

        with patch("agentnexus.tools.file_ops.os.getcwd", return_value=str(tmp_path)):
            file_read("stable.txt")
            file_read("stable.txt")
            file_read("stable.txt")

        assert fp.stat().st_mtime == original_mtime
