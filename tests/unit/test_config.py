import os

import pytest
from pydantic import ValidationError

from agentnexus.core.config import Settings, _config_dir, _default_paths, get_settings


class TestConfigSettings:
    def test_get_settings_returns_valid_settings(self, temp_agentnexus_home):
        s = get_settings()
        assert isinstance(s, Settings)
        assert s.llm_model_id == "deepseek/deepseek-v4-flash"
        assert s.llm_base_url == "https://api.deepseek.com"
        assert s.llm_timeout == 60

    def test_default_field_values(self):
        s = Settings()
        assert s.llm_model_id == "deepseek/deepseek-v4-flash"
        assert s.llm_timeout == 60
        assert s.max_agent_steps == 5
        assert s.enable_query_rewrite is True
        assert s.enable_multi_query is True
        assert s.enable_hyde is False
        assert s.hyde_question_only is True
        assert s.enable_context_expansion is True
        assert s.rag_multi_query_count == 3
        assert s.rag_context_window == 1
        assert s.rag_context_max_chunks == 6
        assert s.embedding_model == "BAAI/bge-small-zh-v1.5"
        assert s.reranker_model == "BAAI/bge-reranker-v2-m3"
        assert s.rag_default_namespace == "default"
        assert s.rag_collection_prefix == "kb_"
        assert s.default_skill == ""
        assert s.skill_auto_route is True
        assert s.skill_auto_route_llm_fallback is True
        assert s.skill_auto_route_min_score == 2.0
        assert s.skill_auto_route_margin == 0.75

    def test_default_rag_storage_paths(self, temp_agentnexus_home):
        s = get_settings()
        assert s.rag_catalog_db_path.startswith(str(temp_agentnexus_home))
        assert s.rag_default_namespace == "default"
        assert s.rag_collection_prefix == "kb_"

    def test_llm_base_url_must_have_scheme(self):
        s = Settings(llm_base_url="https://api.example.com")
        assert s.llm_base_url == "https://api.example.com"

    def test_llm_base_url_trailing_slash_stripped(self):
        s = Settings(llm_base_url="https://api.example.com/")
        assert s.llm_base_url == "https://api.example.com"

    def test_env_prefix_works(self, monkeypatch):
        monkeypatch.setenv("AGENTNEXUS_LLM_MODEL_ID", "gpt-4o")
        s = Settings()
        assert s.llm_model_id == "gpt-4o"

    def test_llm_api_key_is_secretstr(self):
        s = Settings(llm_api_key="sk-test-1234")
        raw = s.llm_api_key.get_secret_value()
        assert raw == "sk-test-1234"
        # repr should not expose the key
        assert "sk-test-1234" not in repr(s.llm_api_key)

    def test_timeout_ge_1(self):
        s = Settings(llm_timeout=1)
        assert s.llm_timeout == 1

    def test_max_agent_steps_bounds(self):
        s = Settings(max_agent_steps=50)
        assert s.max_agent_steps == 50

    def test_mcp_stdio_server_config_parses(self):
        s = Settings(
            mcp_enabled=True,
            mcp_servers=[{
                "name": "demo",
                "transport": "stdio",
                "command": "python",
                "args": ["server.py"],
            }],
        )
        assert s.mcp_enabled is True
        assert len(s.mcp_servers) == 1
        server = s.mcp_servers[0]
        assert server.name == "demo"
        assert server.transport == "stdio"
        assert server.command == "python"
        assert server.args == ["server.py"]
        assert server.import_tools is True
        assert server.import_resources is True
        assert server.import_prompts is True
        assert server.auto_context is True
        assert server.auto_context_max_items == 20
        assert server.auto_context_max_chars == 4000
        assert server.health_check_interval_sec == 30
        assert server.reconnect_initial_delay_sec == 1
        assert server.reconnect_max_delay_sec == 60
        assert server.reconnect_max_attempts == 0
        assert server.max_concurrency_per_server == 4

    def test_mcp_streamable_http_server_config_parses(self):
        s = Settings(
            mcp_enabled=True,
            mcp_servers=[{
                "name": "remote",
                "transport": "streamable-http",
                "url": "https://example.com/mcp/",
                "headers": {"Authorization": "Bearer token"},
            }],
        )
        server = s.mcp_servers[0]
        assert server.transport == "streamable_http"
        assert server.url == "https://example.com/mcp"
        assert server.headers == {"Authorization": "Bearer token"}

    def test_mcp_stdio_requires_command(self):
        with pytest.raises(ValueError, match="command"):
            Settings(mcp_enabled=True, mcp_servers=[{"name": "demo", "transport": "stdio"}])

    def test_mcp_http_requires_url(self):
        with pytest.raises(ValueError, match="url"):
            Settings(mcp_enabled=True, mcp_servers=[{"name": "demo", "transport": "streamable_http"}])

    def test_mcp_sse_remains_unsupported(self):
        with pytest.raises(ValueError, match="transport"):
            Settings(mcp_enabled=True, mcp_servers=[{"name": "old", "transport": "sse", "url": "https://x/mcp"}])


class TestTempAgentnexusHome:
    def test_creates_config_dir(self, temp_agentnexus_home):
        assert temp_agentnexus_home.exists()
        assert temp_agentnexus_home.is_dir()

    def test_sets_env_var(self, temp_agentnexus_home):
        assert "AGENTNEXUS_HOME" in os.environ
        assert os.environ["AGENTNEXUS_HOME"] == str(temp_agentnexus_home)

    def test_config_dir_uses_tmp(self, temp_agentnexus_home):
        d = _config_dir()
        assert str(temp_agentnexus_home) in str(d)

    def test_default_paths_use_correct_dirs(self, temp_agentnexus_home):
        paths = _default_paths()
        base = str(temp_agentnexus_home)
        assert paths["memory_db_path"].startswith(base)
        assert paths["traces_dir"].startswith(base)
        assert paths["chroma_persist_dir"].startswith(base)
        assert paths["rag_catalog_db_path"].startswith(base)


class TestCodeExecutionBackendValidator:
    """Tests for code_execution_backend and shell_execution_backend field validators."""

    def test_code_execution_backend_auto(self, temp_agentnexus_home):
        s = Settings(code_execution_backend="auto")
        assert s.code_execution_backend == "auto"

    def test_code_execution_backend_e2b(self, temp_agentnexus_home):
        s = Settings(code_execution_backend="e2b")
        assert s.code_execution_backend == "e2b"

    def test_code_execution_backend_disabled(self, temp_agentnexus_home):
        s = Settings(code_execution_backend="disabled")
        assert s.code_execution_backend == "disabled"

    def test_code_execution_backend_normalized(self, temp_agentnexus_home):
        s = Settings(code_execution_backend="local-unsafe")
        assert s.code_execution_backend == "local_unsafe"

    def test_code_execution_backend_invalid_raises(self, temp_agentnexus_home):
        with pytest.raises(ValidationError):
            Settings(code_execution_backend="invalid_backend")

    def test_shell_execution_backend_valid(self, temp_agentnexus_home):
        s = Settings(shell_execution_backend="native")
        assert s.shell_execution_backend == "native"

    def test_shell_execution_backend_invalid_raises(self, temp_agentnexus_home):
        with pytest.raises(ValidationError):
            Settings(shell_execution_backend="unknown")

    def test_shell_execution_backend_normalized(self, temp_agentnexus_home):
        s = Settings(shell_execution_backend="local-unsafe")
        assert s.shell_execution_backend == "local_unsafe"

    def test_code_execution_timeout_default(self, temp_agentnexus_home):
        s = Settings()
        assert s.code_execution_timeout == 30

    def test_code_execution_memory_mb_default(self, temp_agentnexus_home):
        s = Settings()
        assert s.code_execution_memory_mb == 256

    def test_code_execution_docker_image_default(self, temp_agentnexus_home):
        s = Settings()
        assert s.code_execution_docker_image == "python:3.11-slim"

    def test_code_execution_allow_unsafe_local_default(self, temp_agentnexus_home):
        s = Settings()
        assert s.code_execution_allow_unsafe_local is False

    def test_shell_execution_memory_default(self, temp_agentnexus_home):
        s = Settings()
        assert s.shell_execution_memory_mb == 256

    def test_shell_execution_docker_image_default(self, temp_agentnexus_home):
        s = Settings()
        assert s.shell_execution_docker_image == "python:3.11-slim"
