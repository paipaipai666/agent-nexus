import os

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
        assert s.embedding_model == "BAAI/bge-small-zh-v1.5"
        assert s.reranker_model == "BAAI/bge-reranker-v2-m3"
        assert s.rag_default_namespace == "default"
        assert s.rag_collection_prefix == "kb_"

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
