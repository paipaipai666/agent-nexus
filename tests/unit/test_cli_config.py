import os

import yaml
import pytest
from typer.testing import CliRunner

from agentnexus.cli import app
import agentnexus.core.config as cfg

runner = CliRunner()


class TestConfigCommand:
    def setup_method(self):
        cfg._settings_cache = None

    def teardown_method(self):
        cfg._settings_cache = None

    def test_view_default(self, temp_agentnexus_home):
        result = runner.invoke(app, ["config"])
        assert result.exit_code == 0
        assert "AgentNexus 配置" in result.stdout
        assert "llm_model_id" in result.stdout
        assert "deepseek/deepseek-v4-flash" in result.stdout
        assert "default" in result.stdout

    def test_view_env_source(self, temp_agentnexus_home, monkeypatch):
        monkeypatch.setenv("AGENTNEXUS_LLM_MODEL_ID", "gpt-4o")
        cfg._settings_cache = None
        result = runner.invoke(app, ["config"])
        assert result.exit_code == 0
        assert "llm_model_id" in result.stdout
        assert "gpt-4o" in result.stdout
        assert "env" in result.stdout

    def test_view_yaml_source(self, temp_agentnexus_home):
        config_dir = temp_agentnexus_home
        yaml_path = config_dir / "config.yaml"
        yaml_path.parent.mkdir(parents=True, exist_ok=True)
        yaml.dump({"llm_model_id": "claude-3-opus"}, yaml_path.open("w", encoding="utf-8"), allow_unicode=True)
        cfg._settings_cache = None
        result = runner.invoke(app, ["config"])
        assert result.exit_code == 0
        assert "llm_model_id" in result.stdout
        assert "claude-3-opus" in result.stdout
        assert "config.yaml" in result.stdout

    def test_set_valid_key(self, temp_agentnexus_home):
        config_dir = temp_agentnexus_home
        result = runner.invoke(app, ["config", "--set", "llm_model_id", "--value", "some-model"])
        assert result.exit_code == 0
        assert "已保存" in result.stdout
        assert "llm_model_id" in result.stdout
        assert "some-model" in result.stdout
        yaml_path = config_dir / "config.yaml"
        assert yaml_path.exists()
        data = yaml.safe_load(yaml_path.open(encoding="utf-8"))
        assert data["llm_model_id"] == "some-model"

    def test_set_invalid_key(self, temp_agentnexus_home):
        result = runner.invoke(app, ["config", "--set", "invalid_key", "--value", "x"])
        assert result.exit_code == 0
        assert "无效" in result.stdout

    def test_set_without_value(self, temp_agentnexus_home):
        result = runner.invoke(app, ["config", "--set", "llm_model_id"])
        assert result.exit_code == 0
        assert "请用 --value" in result.stdout or "请用 -v" in result.stdout


class TestInitCommand:
    def setup_method(self):
        cfg._settings_cache = None

    def teardown_method(self):
        cfg._settings_cache = None

    def test_init(self, temp_agentnexus_home, monkeypatch):
        inputs = iter(["sk-test-key", "my-model", "https://custom.url"])
        monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
        result = runner.invoke(app, ["init"])
        assert result.exit_code == 0
        assert "配置完成" in result.stdout
        config_dir = temp_agentnexus_home
        yaml_path = config_dir / "config.yaml"
        assert yaml_path.exists()
        data = yaml.safe_load(yaml_path.open(encoding="utf-8"))
        assert data["llm_api_key"] == "sk-test-key"
        assert data["llm_model_id"] == "my-model"
        assert data["llm_base_url"] == "https://custom.url"

    def test_init_empty_api_key_retry(self, temp_agentnexus_home, monkeypatch):
        inputs = iter(["", "", "sk-real-key", "model", "url"])
        monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
        result = runner.invoke(app, ["init"])
        assert result.exit_code == 0
        assert "API Key 不能为空" in result.stdout
        assert "配置完成" in result.stdout
