"""Tests for agentnexus.tools.file_ops."""

from pathlib import Path

import pytest

from agentnexus.tools.file_ops import (
    _fingerprint_file,
    _format_size,
    _resolve_safe,
    file_list,
    file_read,
    file_write,
)


class TestResolveSafe:
    def test_valid_path(self):
        p = _resolve_safe(".")
        assert p.is_dir()

    def test_escaping_path_raises(self):
        with pytest.raises(ValueError, match="路径越界"):
            _resolve_safe("..\\..\\etc")


class TestFingerprintFile:
    def test_missing_file(self):
        assert _fingerprint_file(Path("nonexistent_file_xyz")) == "missing"

    def test_existing_file(self, tmp_path: Path):
        f = tmp_path / "test.txt"
        f.write_text("hello")
        fp = _fingerprint_file(f)
        assert len(fp) == 64
        assert fp != "missing"


class TestFileRead:
    def test_file_not_found(self):
        result = file_read("nonexistent_file_xyz123")
        assert "文件不存在" in result

    def test_reads_content(self, tmp_path: Path):
        import os
        os.chdir(tmp_path)
        f = tmp_path / "test.txt"
        f.write_text("line1\nline2\nline3")
        result = file_read("test.txt")
        assert "test.txt" in result
        assert "line1" in result
        assert "3 行" in result

    def test_offset_and_limit(self, tmp_path: Path):
        import os
        os.chdir(tmp_path)
        f = tmp_path / "test.txt"
        f.write_text("a\nb\nc\nd\ne")
        result = file_read("test.txt", offset=2, limit=2)
        assert "c" in result
        assert "d" in result
        # The first displayed line should be line 3 (offset=2), not line 1
        assert "3 | c" in result or "3│c" in result


class TestFileWrite:
    def test_create_new_file(self, tmp_path: Path):
        import os
        os.chdir(tmp_path)
        result = file_write("new.txt", "hello", mode="create")
        assert "已创建" in result
        assert (tmp_path / "new.txt").exists()

    def test_create_existing_fails(self, tmp_path: Path):
        import os
        os.chdir(tmp_path)
        (tmp_path / "existing.txt").write_text("data")
        result = file_write("existing.txt", "hello", mode="create")
        assert "文件已存在" in result

    def test_overwrite(self, tmp_path: Path):
        import os
        os.chdir(tmp_path)
        (tmp_path / "f.txt").write_text("old")
        result = file_write("f.txt", "new", mode="overwrite")
        assert "已覆盖" in result
        assert (tmp_path / "f.txt").read_text() == "new"

    def test_append(self, tmp_path: Path):
        import os
        os.chdir(tmp_path)
        (tmp_path / "f.txt").write_text("base")
        result = file_write("f.txt", "+more", mode="append")
        assert "已追加" in result
        assert (tmp_path / "f.txt").read_text() == "base+more"

    def test_append_nonexistent_fails(self, tmp_path: Path):
        import os
        os.chdir(tmp_path)
        result = file_write("nope.txt", "x", mode="append")
        assert "文件不存在" in result

    def test_invalid_mode(self, tmp_path: Path):
        result = file_write("f.txt", "x", mode="invalid")
        assert "不支持的写入模式" in result

    def test_version_conflict(self, tmp_path: Path):
        import os
        os.chdir(tmp_path)
        (tmp_path / "f.txt").write_text("data")
        result = file_write("f.txt", "new", mode="overwrite", expected_version="wrong")
        assert "版本冲突" in result


class TestFileList:
    def test_nonexistent_dir(self):
        result = file_list("nonexistent_dir_xyz")
        assert "目录不存在" in result

    def test_file_path_raises(self, tmp_path: Path):
        import os
        os.chdir(tmp_path)
        (tmp_path / "f.txt").write_text("x")
        result = file_list("f.txt")
        assert "不是目录" in result

    def test_lists_entries(self, tmp_path: Path):
        import os
        os.chdir(tmp_path)
        (tmp_path / "a.txt").write_text("x")
        (tmp_path / "b.txt").write_text("y")
        result = file_list(".")
        assert "2 项" in result
        assert "a.txt" in result or "A.TXT" in result

    def test_empty_dir(self, tmp_path: Path):
        import os
        os.chdir(tmp_path)
        result = file_list(".")
        assert "(空)" in result


class TestFormatSize:
    def test_bytes(self):
        assert _format_size(500) == "500 B"

    def test_kb(self):
        assert _format_size(2048) == "2.0 KB"

    def test_mb(self):
        assert _format_size(3 * 1024 * 1024) == "3.0 MB"
