"""Tests for agentnexus/cli/kb.py"""
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from agentnexus.cli import app

runner = CliRunner()


class TestKbAdd:
    def test_path_not_exists(self):
        result = runner.invoke(app, ["kb", "add", "/nonexistent/path"])
        assert "路径不存在" in result.stdout

    def test_file_ingestion(self, temp_agentnexus_home):
        filepath = temp_agentnexus_home / "test.md"
        filepath.write_text("# Test\n\nHello world\n")

        fake_artifacts = MagicMock()
        fake_artifacts.chunks = [MagicMock()]
        fake_artifacts.chunks[0].text = "chunk text"
        fake_artifacts.chunks[0].chunk_id = "chunk_001"
        fake_artifacts.document = MagicMock()

        fake_collection = MagicMock()
        fake_collection.count.return_value = 1

        with patch("agentnexus.cli.kb.ingest_document", return_value=fake_artifacts), \
             patch("agentnexus.cli.kb._persist_ingested_document"), \
             patch("agentnexus.cli.kb.get_collection", return_value=fake_collection):
            result = runner.invoke(app, ["kb", "add", str(filepath)])
            assert result.exit_code == 0
            assert "test.md" in result.stdout
            assert "1 个文档块" in result.stdout

    def test_directory_ingestion(self, temp_agentnexus_home):
        docs_dir = temp_agentnexus_home / "docs"
        docs_dir.mkdir()
        (docs_dir / "a.md").write_text("# A\n")
        (docs_dir / "b.md").write_text("# B\n")

        fake_artifacts = MagicMock()
        fake_artifacts.chunks = [MagicMock(), MagicMock()]
        fake_artifacts.chunks[0].text = "chunk 1"
        fake_artifacts.chunks[0].chunk_id = "chunk_001"
        fake_artifacts.chunks[1].text = "chunk 2"
        fake_artifacts.chunks[1].chunk_id = "chunk_002"
        fake_artifacts.document = MagicMock()

        fake_collection = MagicMock()
        fake_collection.count.return_value = 2

        with patch("agentnexus.cli.kb.ingest_document", return_value=fake_artifacts), \
             patch("agentnexus.cli.kb._persist_ingested_document"), \
             patch("agentnexus.cli.kb.get_collection", return_value=fake_collection):
            result = runner.invoke(app, ["kb", "add", str(docs_dir)])
            assert result.exit_code == 0
            assert "a.md" in result.stdout
            assert "b.md" in result.stdout
            assert "2 个文档块" in result.stdout


class TestKbList:
    def test_list_no_data(self, temp_agentnexus_home):
        fake_collection = MagicMock()
        fake_collection.count.return_value = 0

        with patch("agentnexus.cli.kb.get_collection", return_value=fake_collection):
            result = runner.invoke(app, ["kb", "list"])
            assert "知识库" in result.stdout
            assert "0 个文档块" in result.stdout

    def test_list_with_data(self, temp_agentnexus_home):
        fake_collection = MagicMock()
        fake_collection.count.return_value = 5

        with patch("agentnexus.cli.kb.get_collection", return_value=fake_collection):
            result = runner.invoke(app, ["kb", "list"])
            assert "5" in result.stdout
            assert "个文档块" in result.stdout
