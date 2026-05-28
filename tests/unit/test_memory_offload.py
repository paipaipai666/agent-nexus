from agentnexus.memory.offload import offload_large_result


class TestOffloadLargeResult:

    def test_creates_directory_and_file_with_full_content(self, tmp_path):
        offload_dir = str(tmp_path / "offload")
        content = "A" * 2000
        offload_large_result(content, offload_dir, "sess-1")
        files = list((tmp_path / "offload").glob("*.txt"))
        assert len(files) == 1
        assert files[0].read_text(encoding="utf-8") == content

    def test_return_value_contains_cache_marker(self, tmp_path):
        result = offload_large_result("data", str(tmp_path), "sess-1")
        assert "[工具结果已缓存]" in result

    def test_return_value_contains_preview(self, tmp_path):
        content = "Hello " * 200
        result = offload_large_result(content, str(tmp_path), "sess-1")
        assert content[:500] in result

    def test_long_content_preview_truncated_to_500(self, tmp_path):
        content = "x" * 1000
        result = offload_large_result(content, str(tmp_path), "sess-1")
        assert "x" * 500 in result
        assert "x" * 501 not in result

    def test_session_id_in_filename(self, tmp_path):
        offload_large_result("data", str(tmp_path), "my-session-42")
        files = list(tmp_path.glob("*.txt"))
        assert len(files) == 1
        assert "my-session-42" in files[0].name

    def test_nested_directory_creation(self, tmp_path):
        offload_dir = str(tmp_path / "a" / "b" / "c")
        offload_large_result("data", offload_dir, "sess-1")
        files = list((tmp_path / "a" / "b" / "c").glob("*.txt"))
        assert len(files) == 1
