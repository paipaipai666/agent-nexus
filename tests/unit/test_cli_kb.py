"""Tests for agentnexus/cli/kb.py"""
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from agentnexus.cli import app
from agentnexus.rag.models import IngestedDocument

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
        fake_artifacts.document.source_id = "src_test"
        fake_artifacts.document.kb_id = ""

        fake_collection = MagicMock()
        fake_collection.count.return_value = 1

        with patch("agentnexus.cli.kb.ingest_document", return_value=fake_artifacts), \
             patch("agentnexus.cli.kb._persist_ingested_document", return_value={"replaced_chunks": 0, "written_chunks": 1}), \
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
        fake_artifacts.document.source_id = "src_test"
        fake_artifacts.document.kb_id = ""

        fake_collection = MagicMock()
        fake_collection.count.return_value = 2

        with patch("agentnexus.cli.kb.ingest_document", return_value=fake_artifacts), \
             patch("agentnexus.cli.kb._persist_ingested_document", return_value={"replaced_chunks": 0, "written_chunks": 2}), \
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


class TestKbSearch:
    def test_search_empty_kb(self):
        with patch("agentnexus.cli.kb.HybridRetriever") as retriever_cls:
            retriever = MagicMock()
            retriever._chunks = {}
            retriever_cls.return_value = retriever
            result = runner.invoke(app, ["kb", "search", "test"])
            assert result.exit_code == 0
            assert "知识库为空" in result.stdout

    def test_search_outputs_ranked_results(self):
        with patch("agentnexus.cli.kb.HybridRetriever") as retriever_cls, \
             patch("agentnexus.cli.kb.chroma_search") as chroma_search, \
             patch("agentnexus.cli.kb.expand_queries", return_value=["检索用什么", "BM25 查询"]):
            retriever = MagicMock()
            retriever._chunks = {"chunk_1": object()}
            retriever._reranker = None
            retriever.search.return_value = [
                MagicMock(
                    score=0.91,
                    text="BM25 文本检索",
                    metadata={"source_uri": "docs/support.md", "section_title": "检索"},
                )
            ]
            retriever_cls.return_value = retriever
            chroma_search.return_value = [{"id": "chunk_1", "score": 0.8, "text": "BM25 文本检索", "metadata": {}}]

            result = runner.invoke(app, ["kb", "search", "检索用什么", "--top-k", "3"])

            assert result.exit_code == 0
            assert "docs/support.md" in result.stdout
            assert "BM25 文本检索" in result.stdout
            retriever.load_reranker.assert_called_once()
            assert chroma_search.call_count == 2


class TestKbPersistence:
    def test_persist_replaces_previous_source_chunks(self, temp_agentnexus_home):
        from agentnexus.cli import kb as kb_cli
        from agentnexus.rag.chroma_client import chunk_metadata_to_chroma
        from agentnexus.rag.ingestion import ingest_document
        from agentnexus.rag.store import get_knowledge_base_catalog

        old_path = temp_agentnexus_home / "guide.md"
        old_path.write_text("# Guide\n\nOld body\n", encoding="utf-8")
        old_artifacts = ingest_document(str(old_path))

        recorded_ids: list[list[str]] = []

        def fake_upsert(texts, metadatas=None, ids=None, **kwargs):
            recorded_ids.append(list(ids or []))
            return ids or []

        deleted_ids: list[str] = []

        def fake_delete(ids=None, where=None, **kwargs):
            deleted_ids.extend(ids or [])

        with patch("agentnexus.cli.kb.upsert_documents", side_effect=fake_upsert), \
             patch("agentnexus.cli.kb.delete_documents", side_effect=fake_delete):
            kb_cli._persist_ingested_document(old_artifacts, "default")

            old_path.write_text("# Guide\n\nNew body\n", encoding="utf-8")
            new_artifacts = ingest_document(str(old_path))
            stats = kb_cli._persist_ingested_document(new_artifacts, "default")

        catalog = get_knowledge_base_catalog()
        kb = catalog.get_knowledge_base("default")
        assert kb is not None
        documents = catalog.list_documents_by_source(kb.kb_id, new_artifacts.document.source_id)
        assert len(documents) == 1
        assert documents[0].raw_text == "# Guide\n\nNew body\n"
        assert stats["replaced_chunks"] == len(old_artifacts.chunks)
        assert deleted_ids == [old_artifacts.chunks[0].chunk_id]
        assert recorded_ids[-1] == [new_artifacts.chunks[0].chunk_id]

    def test_add_records_ingestion_run(self, temp_agentnexus_home):
        from agentnexus.rag.store import get_knowledge_base_catalog

        filepath = temp_agentnexus_home / "guide.md"
        filepath.write_text("# Guide\n\nBody text\n", encoding="utf-8")

        with patch("agentnexus.cli.kb.upsert_documents", lambda *args, **kwargs: kwargs.get("ids") or []), \
             patch("agentnexus.cli.kb.get_collection") as get_collection:
            get_collection.return_value.count.return_value = 1
            result = runner.invoke(app, ["kb", "add", str(filepath)])

        assert result.exit_code == 0
        catalog = get_knowledge_base_catalog()
        kb = catalog.get_knowledge_base("default")
        assert kb is not None
        runs = catalog.list_ingestion_runs(kb.kb_id)
        assert runs
        assert runs[0].status == "completed"
        assert runs[0].chunks_written >= 1
