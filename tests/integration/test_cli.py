import os

from typer.testing import CliRunner

import agentnexus.cli.kb as kb_cli
import agentnexus.core.config as cfg
import agentnexus.rag.store as rag_store
from agentnexus.cli import app

runner = CliRunner()


class TestCliVersion:
    def test_version_command(self):
        result = runner.invoke(app, ["version"])
        assert result.exit_code == 0
        assert "AgentNexus" in result.stdout
        assert "v0.1.0" in result.stdout


class TestCliStats:
    def test_stats_no_data(self):
        import os

        with runner.isolated_filesystem():
            os.makedirs("traces")
            os.environ["AGENTNEXUS_HOME"] = os.getcwd()
            cfg._settings_cache = None
            try:
                result = runner.invoke(app, ["stats", "--days", "30"])
                assert result.exit_code == 0
                assert "暂无" in result.stdout or "0" in result.stdout
            finally:
                cfg._settings_cache = None
                del os.environ["AGENTNEXUS_HOME"]

    def test_stats_with_trace_data(self):
        import json
        import os
        import time

        with runner.isolated_filesystem():
            os.makedirs("traces")
            trace_dir = os.path.join(os.getcwd(), "traces")
            now = time.time()
            record = {
                "trace_id": "tr-001",
                "span_id": "sp-001",
                "parent_span_id": "",
                "name": "task",
                "start_time": now - 10,
                "end_time": now,
                "latency_ms": 100,
                "input": {"task": "test"},
                "output": {},
                "metadata": {
                    "model": "deepseek-v4-flash",
                    "input_tokens": 100,
                    "output_tokens": 50,
                    "status": "ok",
                },
            }
            date_str = time.strftime("%Y-%m-%d")
            jsonl_path = os.path.join(trace_dir, f"{date_str}.jsonl")
            with open(jsonl_path, "w", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

            os.environ["AGENTNEXUS_HOME"] = os.getcwd()
            cfg._settings_cache = None
            try:
                result = runner.invoke(app, ["stats", "--days", "30"])
                assert result.exit_code == 0
                assert "deepseek" in result.stdout.lower() or "token" in result.stdout.lower()
            finally:
                cfg._settings_cache = None
                del os.environ["AGENTNEXUS_HOME"]


class TestCliConfig:
    def test_config_view(self):
        import os

        with runner.isolated_filesystem():
            os.environ["AGENTNEXUS_HOME"] = os.getcwd()
            try:
                result = runner.invoke(app, ["config"])
                assert result.exit_code == 0
                assert "Key" in result.stdout or "config" in result.stdout.lower()
            finally:
                del os.environ["AGENTNEXUS_HOME"]

    def test_config_set_invalid_key(self):
        import os

        with runner.isolated_filesystem():
            os.environ["AGENTNEXUS_HOME"] = os.getcwd()
            try:
                result = runner.invoke(app, ["config", "--set", "invalid_key", "--value", "x"])
                assert "无效" in result.stdout
            finally:
                del os.environ["AGENTNEXUS_HOME"]

    def test_config_set_without_value(self):
        import os

        with runner.isolated_filesystem():
            os.environ["AGENTNEXUS_HOME"] = os.getcwd()
            try:
                result = runner.invoke(app, ["config", "--set", "llm_model_id"])
                assert "value" in result.stdout.lower()
            finally:
                del os.environ["AGENTNEXUS_HOME"]


class TestCliLogs:
    def test_logs_list_no_data(self):
        import os

        with runner.isolated_filesystem():
            os.makedirs("traces")
            os.environ["AGENTNEXUS_HOME"] = os.getcwd()
            try:
                result = runner.invoke(app, ["logs", "list", "--days", "30"])
                assert result.exit_code == 0
                assert "暂无" in result.stdout
            finally:
                del os.environ["AGENTNEXUS_HOME"]


class TestCliEvaluation:
    def test_eval_run_uses_isolated_namespace(self, monkeypatch):
        captured = []

        class FakeRun:
            def __init__(self):
                from agentnexus.rag.ingestion import ChunkStrategy
                self.label = "fixed-256-dense"
                self.strategy = ChunkStrategy.FIXED
                self.chunk_size = 256
                self.use_hybrid = False
                self.faithfulness = 1.0
                self.answer_relevancy = 1.0
                self.context_precision = 1.0
                self.context_recall = 1.0
                self.context_relevancy = 1.0
                self.avg_latency_ms = 1.0
                self.p95_latency_ms = 1.0
                self.rejection_rate = 1.0
                self.faithfulness_ci = (1.0, 1.0)
                self.answer_relevancy_ci = (1.0, 1.0)
                self.context_precision_ci = (1.0, 1.0)
                self.context_recall_ci = (1.0, 1.0)
                self.context_relevancy_ci = (1.0, 1.0)

        class FakeEvaluator:
            def __init__(self, documents, samples):
                self.documents = documents
                self.samples = samples

            def run_combination(self, strategy, chunk_size, overlap, use_hybrid, _token_budget=None):
                captured.append((strategy.value, chunk_size, use_hybrid))
                return FakeRun()

        import agentnexus.cli.eval_cmd as eval_cmd
        monkeypatch.setattr(eval_cmd, "RAGEvaluator", FakeEvaluator, raising=False)

        with runner.isolated_filesystem():
            os.environ["AGENTNEXUS_HOME"] = os.getcwd()
            cfg._settings_cache = None
            try:
                result = runner.invoke(app, ["eval", "run"])
                assert result.exit_code == 0
                assert captured
            finally:
                cfg._settings_cache = None
                del os.environ["AGENTNEXUS_HOME"]


class TestCliKnowledgeBase:
    def test_kb_add_persists_structured_artifacts(self, monkeypatch):
        captured: dict[str, object] = {}

        class FakeCollection:
            def __init__(self):
                self.count_value = 0

            def count(self):
                return self.count_value

        fake_collection = FakeCollection()

        def fake_write_documents(texts, metadatas=None, ids=None, name=None, namespace=None, metadata=None):
            captured["texts"] = texts
            captured["metadatas"] = metadatas
            captured["ids"] = ids
            captured["namespace"] = namespace
            fake_collection.count_value = len(texts)
            return ids or [f"generated-{index}" for index in range(len(texts))]

        monkeypatch.setattr(kb_cli, "upsert_documents", fake_write_documents)
        monkeypatch.setattr(kb_cli, "get_collection", lambda *args, **kwargs: fake_collection)

        with runner.isolated_filesystem():
            cfg._settings_cache = None
            os.environ["AGENTNEXUS_HOME"] = os.getcwd()
            rag_store._reset_knowledge_base_catalog()
            try:
                os.makedirs("docs")
                doc_path = os.path.join("docs", "guide.md")
                with open(doc_path, "w", encoding="utf-8") as f:
                    f.write("# Guide\n\nBody text\n")

                result = runner.invoke(app, ["kb", "add", doc_path])

                assert result.exit_code == 0
                catalog = rag_store.get_knowledge_base_catalog()
                documents = catalog.list_documents()
                assert len(documents) == 1
                document = documents[0]
                assert document.raw_text == "# Guide\n\nBody text\n"
                assert document.indexed_text == "Guide\n\nBody text"
                assert document.sections[0].metadata["heading_path"] == ["Guide"]

                chunks = catalog.list_chunks(document.document_id)
                assert chunks
                assert chunks[0].chunk_id.startswith("chunk_")
                assert chunks[0].section_index == 0
                assert chunks[0].metadata["heading_path"] == ["Guide"]

                assert captured["ids"] == [chunks[0].chunk_id]
                assert captured["namespace"] == "default"
                metadatas = captured["metadatas"]
                assert isinstance(metadatas, list)
                assert metadatas[0]["heading_path_text"] == "Guide"
                assert "heading_path" not in metadatas[0]
            finally:
                rag_store._reset_knowledge_base_catalog()
                cfg._settings_cache = None
                del os.environ["AGENTNEXUS_HOME"]
