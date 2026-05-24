import os
import tempfile
from pathlib import Path

import yaml
from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AGENTNEXUS_", extra="ignore")

    llm_api_key: SecretStr = Field(default=SecretStr(""))
    llm_model_id: str = Field(default="deepseek/deepseek-v4-flash")
    llm_base_url: str = Field(default="https://api.deepseek.com")
    llm_timeout: int = Field(default=60, ge=1)
    # Model capability overrides (None = auto-detect)
    model_tool_calling: bool | None = Field(default=None)
    model_json_mode: bool | None = Field(default=None)
    model_thinking: bool | None = Field(default=None)
    model_thinking_budget: int = Field(default=4000, ge=1024, le=32000)
    judge_model_id: str = Field(default="zhipu/glm-4.7-flash")
    judge_api_key: SecretStr = Field(default=SecretStr(""))
    judge_base_url: str = Field(default="https://open.bigmodel.cn/api/paas/v4/")
    tavily_api_key: SecretStr = Field(default=SecretStr(""))
    e2b_api_key: SecretStr = Field(default=SecretStr(""))
    max_agent_steps: int = Field(default=5, ge=1, le=50)
    enable_contextual_retrieval: bool = Field(default=False)
    embedding_model: str = Field(default="BAAI/bge-small-zh-v1.5")
    reranker_model: str = Field(default="BAAI/bge-reranker-v2-m3")
    chroma_persist_dir: str = Field(default="")
    memory_db_path: str = Field(default="")
    traces_dir: str = Field(default="")
    rag_catalog_db_path: str = Field(default="")
    rag_default_namespace: str = Field(default="default")
    rag_collection_prefix: str = Field(default="kb_")
    max_memories: int = Field(default=1000, ge=100, le=100000)
    memory_ttl_days: int = Field(default=90, ge=7, le=365)
    trace_retention_days: int = Field(default=30, ge=1, le=365)
    # Compaction tuning
    autocompact_buffer_tokens: int = Field(default=8000, ge=1000, le=100000)
    large_result_threshold: int = Field(default=10240, ge=1024, le=1048576)
    offload_enabled: bool = Field(default=True)
    # Snip & time-based microcompact
    snip_enabled: bool = Field(default=True)
    time_microcompact_interval: int = Field(default=300, ge=60, le=3600)
    # Post-compact file recovery
    post_compact_max_files: int = Field(default=5, ge=1, le=100)
    post_compact_token_per_file: int = Field(default=5000, ge=500, le=50000)
    post_compact_token_budget: int = Field(default=50000, ge=1000, le=200000)
    # Kairos transcript backup
    transcript_enabled: bool = Field(default=True)
    # Shell execution
    shell_enabled: bool = Field(default=True)
    shell_confirm: bool = Field(default=True)
    shell_timeout: int = Field(default=30, ge=1, le=300)
    # File operations
    file_read_max_mb: float = Field(default=10.0, ge=1, le=100)
    # Shell blacklist (regex patterns, checked case-insensitive)
    shell_blacklist: list[str] = Field(default_factory=list)

    @field_validator("llm_base_url", "judge_base_url")
    @classmethod
    def must_have_scheme(cls, v: str) -> str:
        if v and not v.startswith(("http://", "https://")):
            raise ValueError(f"必须以 http:// 或 https:// 开头: {v}")
        return v.rstrip("/")


class AgentNexusDumper(yaml.SafeDumper):
    pass


def _dump_secret_str(dumper: yaml.Dumper, value: SecretStr):
    return dumper.represent_scalar("tag:yaml.org,2002:str", str(value))


AgentNexusDumper.add_representer(SecretStr, _dump_secret_str)
yaml.add_representer(SecretStr, _dump_secret_str, Dumper=yaml.Dumper)
yaml.add_representer(SecretStr, _dump_secret_str, Dumper=yaml.SafeDumper)


def _config_dir() -> Path:
    d = Path(os.environ.get("AGENTNEXUS_HOME", Path.home() / ".agentnexus"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def _set_restrictive_permissions(path: Path) -> None:
    mode = 0o400 if os.name == "nt" else 0o600
    try:
        os.chmod(path, mode)
    except OSError:
        pass


def _write_yaml_config(data: dict) -> Path:
    config_path = _config_dir() / "config.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)

    if config_path.exists():
        try:
            os.chmod(config_path, 0o600)
        except OSError:
            pass

    fd, tmp_name = tempfile.mkstemp(dir=config_path.parent, prefix="config.", suffix=".tmp")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            yaml.dump(data, f, Dumper=AgentNexusDumper, allow_unicode=True, sort_keys=True)
        _set_restrictive_permissions(tmp_path)
        tmp_path.replace(config_path)
        _set_restrictive_permissions(config_path)
        global _settings_cache
        _settings_cache = None
        return config_path
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _default_paths() -> dict:
    d = _config_dir()
    return {
        "chroma_persist_dir": str(d / "chroma"),
        "memory_db_path": str(d / "memory.db"),
        "traces_dir": str(d / "traces"),
        "rag_catalog_db_path": str(d / "rag_catalog.db"),
    }


def _load_yaml() -> dict:
    yaml_path = _config_dir() / "config.yaml"
    if yaml_path.exists():
        with open(yaml_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


_settings_cache: Settings | None = None


def get_settings() -> Settings:
    global _settings_cache
    if _settings_cache is None:
        data = _load_yaml()
        _settings_cache = Settings(**data, **_default_paths())
    return _settings_cache
