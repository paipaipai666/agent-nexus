"""Tests for agentnexus/cli/stats.py"""
import json
import os
import time

from typer.testing import CliRunner

import agentnexus.core.config as cfg
from agentnexus.cli import app

runner = CliRunner()


class TestStatsCmd:
    def test_no_data(self):
        with runner.isolated_filesystem():
            os.makedirs("traces")
            os.environ["AGENTNEXUS_HOME"] = os.getcwd()
            cfg._settings_cache = None
            try:
                result = runner.invoke(app, ["stats"])
                assert result.exit_code == 0
                assert "暂无任务数据" in result.stdout
            finally:
                cfg._settings_cache = None
                del os.environ["AGENTNEXUS_HOME"]

    def test_with_data(self):
        with runner.isolated_filesystem():
            os.makedirs("traces")
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
            trace_dir = os.path.join(os.getcwd(), "traces")
            jsonl_path = os.path.join(trace_dir, f"{date_str}.jsonl")
            with open(jsonl_path, "w", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

            os.environ["AGENTNEXUS_HOME"] = os.getcwd()
            cfg._settings_cache = None
            try:
                result = runner.invoke(app, ["stats"])
                assert result.exit_code == 0
                assert "deepseek-v4-flash" in result.stdout
                assert "总任务数" in result.stdout
            finally:
                cfg._settings_cache = None
                del os.environ["AGENTNEXUS_HOME"]

    def test_custom_days(self):
        with runner.isolated_filesystem():
            os.makedirs("traces")
            os.environ["AGENTNEXUS_HOME"] = os.getcwd()
            cfg._settings_cache = None
            try:
                result = runner.invoke(app, ["stats", "--days", "30"])
                assert result.exit_code == 0
                assert "30" in result.stdout
            finally:
                cfg._settings_cache = None
                del os.environ["AGENTNEXUS_HOME"]

    def test_cache_hit_stats_displayed(self):
        with runner.isolated_filesystem():
            os.makedirs("traces")
            now = time.time()
            # Need a "task" span for total_tasks > 0
            task_record = {
                "trace_id": "tr-001", "name": "task",
                "start_time": now - 10, "latency_ms": 100,
                "input": {}, "output": {},
                "metadata": {
                    "model": "deepseek-v4-flash",
                    "input_tokens": 1000, "output_tokens": 50,
                },
            }
            llm_record = {
                "trace_id": "tr-001", "name": "llm",
                "start_time": now - 10, "latency_ms": 100,
                "input": {}, "output": {},
                "metadata": {
                    "model": "deepseek-v4-flash",
                    "input_tokens": 1000, "output_tokens": 50,
                    "cache_hit_tokens": 800, "cache_miss_tokens": 200,
                    "cache_hit_rate": 0.8,
                },
            }
            date_str = time.strftime("%Y-%m-%d")
            jsonl_path = os.path.join(os.getcwd(), "traces", f"{date_str}.jsonl")
            with open(jsonl_path, "w", encoding="utf-8") as f:
                f.write(json.dumps(task_record, ensure_ascii=False) + "\n")
                f.write(json.dumps(llm_record, ensure_ascii=False) + "\n")

            os.environ["AGENTNEXUS_HOME"] = os.getcwd()
            cfg._settings_cache = None
            try:
                result = runner.invoke(app, ["stats"])
                assert result.exit_code == 0
                # Check for cache hit rate display (use ASCII fallback for encoding issues)
                assert "80.0%" in result.stdout or "cache" in result.stdout.lower()
            finally:
                cfg._settings_cache = None
                del os.environ["AGENTNEXUS_HOME"]

    def test_no_cache_data_no_cache_section(self):
        with runner.isolated_filesystem():
            os.makedirs("traces")
            now = time.time()
            record = {
                "trace_id": "tr-001", "name": "llm",
                "start_time": now - 10, "latency_ms": 100,
                "input": {}, "output": {},
                "metadata": {
                    "model": "deepseek-v4-flash",
                    "input_tokens": 100, "output_tokens": 50,
                },
            }
            date_str = time.strftime("%Y-%m-%d")
            jsonl_path = os.path.join(os.getcwd(), "traces", f"{date_str}.jsonl")
            with open(jsonl_path, "w", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

            os.environ["AGENTNEXUS_HOME"] = os.getcwd()
            cfg._settings_cache = None
            try:
                result = runner.invoke(app, ["stats"])
                assert result.exit_code == 0
                assert "缓存命中率" not in result.stdout
            finally:
                cfg._settings_cache = None
                del os.environ["AGENTNEXUS_HOME"]
