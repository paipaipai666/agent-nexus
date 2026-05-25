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
                self.answer_correctness = 1.0
                self.context_precision = 1.0
                self.context_recall = 1.0
                self.context_relevancy = 1.0
                self.hit_rate = 1.0
                self.mrr = 1.0
                self.avg_latency_ms = 1.0
                self.p95_latency_ms = 1.0
                self.rejection_rate = 1.0
                self.faithfulness_ci = (1.0, 1.0)
                self.answer_relevancy_ci = (1.0, 1.0)
                self.answer_correctness_ci = (1.0, 1.0)
                self.context_precision_ci = (1.0, 1.0)
                self.context_recall_ci = (1.0, 1.0)
                self.context_relevancy_ci = (1.0, 1.0)
                self.hit_rate_ci = (1.0, 1.0)
                self.mrr_ci = (1.0, 1.0)

            def check_passed(self, thresholds=None):
                return True

        class FakeEvaluator:
            def __init__(self, documents, samples):
                self.documents = documents
                self.samples = samples

            def run_combination(self, strategy, chunk_size, overlap, use_hybrid, _token_budget=None, top_k=10):
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


class TestCliEvalRun:
    def test_eval_run_with_mocked_evaluator_exits_0(self, monkeypatch):
        import agentnexus.cli.eval_cmd as eval_cmd

        class FakeRun:
            def __init__(self):
                from agentnexus.rag.ingestion import ChunkStrategy
                self.label = "fixed-256-dense"
                self.strategy = ChunkStrategy.FIXED
                self.chunk_size = 256
                self.use_hybrid = False
                self.faithfulness = 1.0
                self.answer_relevancy = 1.0
                self.answer_correctness = 1.0
                self.context_precision = 1.0
                self.context_recall = 1.0
                self.context_relevancy = 1.0
                self.hit_rate = 1.0
                self.mrr = 1.0
                self.avg_latency_ms = 1.0
                self.p95_latency_ms = 1.0
                self.rejection_rate = 0.0
                self.faithfulness_ci = (1.0, 1.0)
                self.answer_relevancy_ci = (1.0, 1.0)
                self.answer_correctness_ci = (1.0, 1.0)
                self.context_precision_ci = (1.0, 1.0)
                self.context_recall_ci = (1.0, 1.0)
                self.context_relevancy_ci = (1.0, 1.0)
                self.hit_rate_ci = (1.0, 1.0)
                self.mrr_ci = (1.0, 1.0)

            def check_passed(self, thresholds=None):
                return True

        class FakeEvaluator:
            def __init__(self, documents, samples):
                self.documents = documents
                self.samples = samples

            def run_combination(self, strategy, chunk_size, overlap, use_hybrid, _token_budget=None, top_k=10):
                return FakeRun()

        monkeypatch.setattr(eval_cmd, "RAGEvaluator", FakeEvaluator, raising=False)

        with runner.isolated_filesystem():
            os.environ["AGENTNEXUS_HOME"] = os.getcwd()
            cfg._settings_cache = None
            try:
                result = runner.invoke(app, ["eval", "run"])
                assert result.exit_code == 0
                assert "评估" in result.stdout or "RAG" in result.stdout
            finally:
                cfg._settings_cache = None
                del os.environ["AGENTNEXUS_HOME"]

    def test_eval_run_with_ci_flag(self, monkeypatch):
        import agentnexus.cli.eval_cmd as eval_cmd

        class FakeRun:
            def __init__(self):
                from agentnexus.rag.ingestion import ChunkStrategy
                self.label = "test"
                self.strategy = ChunkStrategy.FIXED
                self.chunk_size = 256
                self.use_hybrid = False
                self.faithfulness = 0.5
                self.answer_relevancy = 0.5
                self.answer_correctness = 0.5
                self.context_precision = 0.5
                self.context_recall = 0.5
                self.context_relevancy = 0.5
                self.hit_rate = 0.5
                self.mrr = 0.5
                self.avg_latency_ms = 100.0
                self.p95_latency_ms = 200.0
                self.rejection_rate = 0.0
                self.faithfulness_ci = (0.4, 0.6)
                self.answer_relevancy_ci = (0.4, 0.6)
                self.answer_correctness_ci = (0.4, 0.6)
                self.context_precision_ci = (0.4, 0.6)
                self.context_recall_ci = (0.4, 0.6)
                self.context_relevancy_ci = (0.4, 0.6)
                self.hit_rate_ci = (0.4, 0.6)
                self.mrr_ci = (0.4, 0.6)

            def check_passed(self, thresholds=None):
                return True

        class FakeEvaluator:
            def __init__(self, documents, samples):
                self.documents = documents
                self.samples = samples

            def run_combination(self, strategy, chunk_size, overlap, use_hybrid, _token_budget=None, top_k=10):
                return FakeRun()

        monkeypatch.setattr(eval_cmd, "RAGEvaluator", FakeEvaluator, raising=False)

        with runner.isolated_filesystem():
            os.environ["AGENTNEXUS_HOME"] = os.getcwd()
            cfg._settings_cache = None
            try:
                result = runner.invoke(app, ["eval", "run", "--ci"])
                assert result.exit_code == 0
            finally:
                cfg._settings_cache = None
                del os.environ["AGENTNEXUS_HOME"]


class TestCliEvalHistory:
    def test_eval_history_no_data(self):
        with runner.isolated_filesystem():
            os.environ["AGENTNEXUS_HOME"] = os.getcwd()
            cfg._settings_cache = None
            try:
                result = runner.invoke(app, ["eval", "history"])
                assert result.exit_code == 0
                assert "暂无" in result.stdout
            finally:
                cfg._settings_cache = None
                del os.environ["AGENTNEXUS_HOME"]

    def test_eval_history_with_fake_data(self):
        import json
        import os

        with runner.isolated_filesystem():
            traces_dir = os.path.join(os.getcwd(), "traces")
            evals_dir = os.path.join(traces_dir, "evals")
            os.makedirs(evals_dir, exist_ok=True)
            report = {
                "dataset_version": "v1",
                "top_k": 10,
                "configs": [
                    {
                        "label": "fixed-256-dense",
                        "strategy": "fixed",
                        "chunk_size": 256,
                        "use_hybrid": False,
                        "faithfulness": 0.95,
                        "hit_rate": 0.9,
                        "mrr": 0.85,
                    }
                ],
            }
            with open(os.path.join(evals_dir, "eval_report_20240101_120000.json"), "w", encoding="utf-8") as f:
                json.dump(report, f, ensure_ascii=False)

            os.environ["AGENTNEXUS_HOME"] = os.getcwd()
            cfg._settings_cache = None
            try:
                result = runner.invoke(app, ["eval", "history"])
                assert result.exit_code == 0
                assert "fixed-256" in result.stdout
            finally:
                cfg._settings_cache = None
                del os.environ["AGENTNEXUS_HOME"]


class TestCliEvalCompare:
    def test_eval_compare_with_missing_baseline(self):
        with runner.isolated_filesystem():
            os.environ["AGENTNEXUS_HOME"] = os.getcwd()
            cfg._settings_cache = None
            try:
                result = runner.invoke(app, [
                    "eval", "compare",
                    "--baseline", "nonexistent.json",
                    "--candidate", "nonexistent.json",
                ])
                assert result.exit_code == 0
                assert "基准文件不存在" in result.stdout
            finally:
                cfg._settings_cache = None
                del os.environ["AGENTNEXUS_HOME"]

    def test_eval_compare_with_missing_candidate(self):
        import json
        import os

        with runner.isolated_filesystem():
            baseline = os.path.join(os.getcwd(), "baseline.json")
            with open(baseline, "w", encoding="utf-8") as f:
                json.dump({"configs": []}, f)

            os.environ["AGENTNEXUS_HOME"] = os.getcwd()
            cfg._settings_cache = None
            try:
                result = runner.invoke(app, [
                    "eval", "compare",
                    "--baseline", baseline,
                    "--candidate", "nonexistent.json",
                ])
                assert result.exit_code == 0
                assert "候选文件不存在" in result.stdout
            finally:
                cfg._settings_cache = None
                del os.environ["AGENTNEXUS_HOME"]

    def test_eval_compare_with_equal_data(self):
        import json
        import os

        with runner.isolated_filesystem():
            baseline = os.path.join(os.getcwd(), "baseline.json")
            candidate = os.path.join(os.getcwd(), "candidate.json")
            report_data = {
                "dataset_version": "v1",
                "configs": [
                    {
                        "label": "fixed-256-dense",
                        "faithfulness": 0.9,
                        "answer_relevancy": 0.85,
                        "hit_rate": 0.8,
                        "mrr": 0.75,
                        "context_precision": 0.85,
                        "context_recall": 0.8,
                    }
                ],
            }
            with open(baseline, "w", encoding="utf-8") as f:
                json.dump(report_data, f, ensure_ascii=False)
            with open(candidate, "w", encoding="utf-8") as f:
                json.dump(report_data, f, ensure_ascii=False)

            os.environ["AGENTNEXUS_HOME"] = os.getcwd()
            cfg._settings_cache = None
            try:
                result = runner.invoke(app, [
                    "eval", "compare",
                    "--baseline", baseline,
                    "--candidate", candidate,
                ])
                assert result.exit_code == 0
                assert "fixed-25" in result.stdout
            finally:
                cfg._settings_cache = None
                del os.environ["AGENTNEXUS_HOME"]


class TestCliLogsView:
    def test_logs_view_with_valid_trace(self):
        import json
        import os
        import time

        with runner.isolated_filesystem():
            os.makedirs("traces")
            trace_dir = os.path.join(os.getcwd(), "traces")
            now = time.time()
            spans = [
                {
                    "trace_id": "tr-view-001",
                    "span_id": "sp-root",
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
                },
                {
                    "trace_id": "tr-view-001",
                    "span_id": "sp-llm-1",
                    "parent_span_id": "sp-root",
                    "name": "llm",
                    "start_time": now - 8,
                    "end_time": now - 7,
                    "latency_ms": 50,
                    "input": {},
                    "output": {},
                    "metadata": {
                        "model": "deepseek-v4-flash",
                        "input_tokens": 50,
                        "output_tokens": 25,
                        "status": "ok",
                    },
                },
            ]
            date_str = time.strftime("%Y-%m-%d")
            jsonl_path = os.path.join(trace_dir, f"{date_str}.jsonl")
            with open(jsonl_path, "w", encoding="utf-8") as f:
                for span in spans:
                    f.write(json.dumps(span, ensure_ascii=False) + "\n")

            os.environ["AGENTNEXUS_HOME"] = os.getcwd()
            cfg._settings_cache = None
            try:
                result = runner.invoke(app, ["logs", "view", "--trace-id", "tr-view-001"])
                assert result.exit_code == 0
                assert "tr-view-001" in result.stdout
                assert "task" in result.stdout.lower() or "llm" in result.stdout.lower()
            finally:
                cfg._settings_cache = None
                del os.environ["AGENTNEXUS_HOME"]

    def test_logs_view_with_invalid_trace(self):
        import os

        with runner.isolated_filesystem():
            os.makedirs("traces")
            os.environ["AGENTNEXUS_HOME"] = os.getcwd()
            cfg._settings_cache = None
            try:
                result = runner.invoke(app, ["logs", "view", "--trace-id", "nonexistent"])
                assert result.exit_code == 0
                assert "未找到" in result.stdout or "暂无" in result.stdout
            finally:
                cfg._settings_cache = None
                del os.environ["AGENTNEXUS_HOME"]

    def test_logs_view_no_traces_dir(self):
        import os

        with runner.isolated_filesystem():
            os.environ["AGENTNEXUS_HOME"] = os.getcwd()
            cfg._settings_cache = None
            try:
                result = runner.invoke(app, ["logs", "view", "--trace-id", "any"])
                assert result.exit_code == 0
                assert "暂无" in result.stdout
            finally:
                cfg._settings_cache = None
                del os.environ["AGENTNEXUS_HOME"]
