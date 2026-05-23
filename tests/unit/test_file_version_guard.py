from pathlib import Path

from agentnexus.tools.file_ops import file_read, file_write


class TestFileVersionGuard:
    def test_file_read_includes_version(self, temp_agentnexus_home):
        file_path = Path("sample.txt")
        file_path.write_text("hello\nworld\n", encoding="utf-8")

        result = file_read("sample.txt")

        assert "version=" in result
        assert "hello" in result

    def test_file_write_rejects_stale_expected_version(self, temp_agentnexus_home):
        file_path = Path("sample.txt")
        file_path.write_text("original", encoding="utf-8")

        first_read = file_read("sample.txt")
        expected_version = first_read.split("version=")[1].split(")")[0]

        file_path.write_text("changed by someone else", encoding="utf-8")
        result = file_write("sample.txt", "new content", mode="overwrite", expected_version=expected_version)

        assert "文件版本冲突" in result
        assert "期望版本=" in result
        assert "当前版本=" in result

    def test_file_write_accepts_matching_expected_version(self, temp_agentnexus_home):
        file_path = Path("sample.txt")
        file_path.write_text("original", encoding="utf-8")

        first_read = file_read("sample.txt")
        expected_version = first_read.split("version=")[1].split(")")[0]

        result = file_write("sample.txt", "new content", mode="overwrite", expected_version=expected_version)

        assert result.startswith("[file_write] 已覆盖 sample.txt")
        assert "version=" in result
        assert file_path.read_text(encoding="utf-8") == "new content"
