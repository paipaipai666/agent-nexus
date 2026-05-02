from typer.testing import CliRunner

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
            try:
                result = runner.invoke(app, ["stats", "--days", "30"])
                assert result.exit_code == 0
                assert "暂无" in result.stdout or "0" in result.stdout
            finally:
                del os.environ["AGENTNEXUS_HOME"]

    def test_stats_with_trace_data(self):
        import os
        import json
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
            try:
                result = runner.invoke(app, ["stats", "--days", "30"])
                assert result.exit_code == 0
                assert "deepseek" in result.stdout.lower() or "token" in result.stdout.lower()
            finally:
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
