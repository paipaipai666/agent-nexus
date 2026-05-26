import os
import tempfile
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class MCPServerConfig(BaseModel):
    name: str
    enabled: bool = True
    transport: str = Field(default="stdio")
    command: str | None = Field(default=None)
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    cwd: str | None = Field(default=None)
    url: str | None = Field(default=None)
    headers: dict[str, str] = Field(default_factory=dict)
    tool_prefix: str | None = Field(default=None)
    include_tools: list[str] = Field(default_factory=list)
    exclude_tools: list[str] = Field(default_factory=list)
    import_tools: bool = Field(default=True)
    import_resources: bool = Field(default=True)
    import_prompts: bool = Field(default=True)
    auto_context: bool = Field(default=True)
    auto_context_max_items: int = Field(default=20, ge=0, le=200)
    auto_context_max_chars: int = Field(default=4000, ge=0, le=50000)
    health_check_interval_sec: int = Field(default=30, ge=1, le=3600)
    reconnect_initial_delay_sec: int = Field(default=1, ge=1, le=3600)
    reconnect_max_delay_sec: int = Field(default=60, ge=1, le=3600)
    reconnect_max_attempts: int = Field(default=0, ge=0, le=1000000)
    max_concurrency_per_server: int = Field(default=4, ge=1, le=100)
    allowed_agents: list[str] = Field(
        default_factory=lambda: ["react_agent", "subagent_explorer", "subagent_executor"]
    )
    risk_level: str = Field(default="medium")
    require_hitl: bool = Field(default=False)
    timeout_sec: int = Field(default=60, ge=1, le=600)
    rate_limit_per_min: int = Field(default=10, ge=0, le=1000)

    @field_validator("transport")
    @classmethod
    def normalize_transport(cls, value: str) -> str:
        normalized = (value or "stdio").strip().lower().replace("-", "_")
        if normalized == "http":
            normalized = "streamable_http"
        if normalized not in {"stdio", "streamable_http"}:
            raise ValueError(f"不支持的 MCP transport: {value}")
        return normalized

    @field_validator("risk_level")
    @classmethod
    def normalize_risk_level(cls, value: str) -> str:
        normalized = (value or "medium").strip().lower()
        if normalized not in {"low", "medium", "high"}:
            raise ValueError(f"不支持的风险等级: {value}")
        return normalized

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str | None) -> str | None:
        if not value:
            return value
        if not value.startswith(("http://", "https://")):
            raise ValueError(f"MCP URL 必须以 http:// 或 https:// 开头: {value}")
        return value.rstrip("/")

    @model_validator(mode="after")
    def validate_transport_requirements(self):
        if self.transport == "stdio" and not self.command:
            raise ValueError("stdio MCP server 必须提供 command")
        if self.transport == "streamable_http" and not self.url:
            raise ValueError("Streamable HTTP MCP server 必须提供 url")
        return self


class LLMSettings(BaseModel):
    api_key: SecretStr
    model_id: str
    base_url: str
    timeout: int
    model_tool_calling: bool | None
    model_json_mode: bool | None
    model_thinking: bool | None
    model_thinking_budget: int
    judge_model_id: str
    judge_api_key: SecretStr
    judge_base_url: str


class RAGSettings(BaseModel):
    enable_contextual_retrieval: bool
    enable_query_rewrite: bool
    enable_multi_query: bool
    enable_hyde: bool
    hyde_question_only: bool
    enable_context_expansion: bool
    multi_query_count: int
    context_window: int
    context_max_chunks: int
    embedding_model: str
    reranker_model: str
    chroma_persist_dir: str
    catalog_db_path: str
    default_namespace: str
    collection_prefix: str


class MemorySettings(BaseModel):
    db_path: str
    max_memories: int
    ttl_days: int
    autocompact_buffer_tokens: int
    large_result_threshold: int
    offload_enabled: bool
    snip_enabled: bool
    time_microcompact_interval: int
    post_compact_max_files: int
    post_compact_token_per_file: int
    post_compact_token_budget: int
    transcript_enabled: bool


class RuntimeSettings(BaseModel):
    max_agent_steps: int
    traces_dir: str
    trace_retention_days: int
    shell_enabled: bool
    shell_confirm: bool
    shell_timeout: int
    code_execution_backend: str
    code_execution_timeout: int
    code_execution_memory_mb: int
    code_execution_docker_image: str
    code_execution_allow_unsafe_local: bool
    shell_execution_backend: str
    shell_execution_memory_mb: int
    shell_execution_docker_image: str
    file_read_max_mb: float
    shell_blacklist: list[str]
    runtime_profile: str


class MCPSettings(BaseModel):
    enabled: bool
    startup_timeout: int
    servers: list[MCPServerConfig]


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
    enable_query_rewrite: bool = Field(default=True)
    enable_multi_query: bool = Field(default=True)
    enable_hyde: bool = Field(default=False)
    hyde_question_only: bool = Field(default=True)
    enable_context_expansion: bool = Field(default=True)
    rag_multi_query_count: int = Field(default=3, ge=1, le=5)
    rag_context_window: int = Field(default=1, ge=0, le=3)
    rag_context_max_chunks: int = Field(default=6, ge=1, le=12)
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
    mcp_enabled: bool = Field(default=False)
    mcp_startup_timeout: int = Field(default=15, ge=1, le=300)
    mcp_servers: list[MCPServerConfig] = Field(default_factory=list)
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
    # Python code execution
    # auto: e2b -> native OS sandbox -> docker -> disabled
    code_execution_backend: str = Field(default="auto")
    code_execution_timeout: int = Field(default=30, ge=1, le=300)
    code_execution_memory_mb: int = Field(default=256, ge=64, le=8192)
    code_execution_docker_image: str = Field(default="python:3.11-slim")
    code_execution_allow_unsafe_local: bool = Field(default=False)
    shell_execution_backend: str = Field(default="auto")
    shell_execution_memory_mb: int = Field(default=256, ge=64, le=8192)
    shell_execution_docker_image: str = Field(default="python:3.11-slim")
    # File operations
    file_read_max_mb: float = Field(default=10.0, ge=1, le=100)
    # Shell blacklist (regex patterns, checked case-insensitive)
    shell_blacklist: list[str] = Field(default_factory=list)
    # Declarative extensions and workflow defaults
    extensions_enabled: bool = Field(default=True)
    extensions_dirs: list[str] = Field(default_factory=list)
    plugins_auto_discover: bool = Field(default=True)
    skills_default_namespace: str = Field(default="default")
    default_skill: str = Field(default="")
    skill_auto_route: bool = Field(default=True)
    skill_auto_route_llm_fallback: bool = Field(default=True)
    skill_auto_route_min_score: float = Field(default=2.0, ge=0.1, le=100.0)
    skill_auto_route_margin: float = Field(default=0.75, ge=0.0, le=100.0)
    runtime_profile: str = Field(default="default")

    @field_validator("llm_base_url", "judge_base_url")
    @classmethod
    def must_have_scheme(cls, v: str) -> str:
        if v and not v.startswith(("http://", "https://")):
            raise ValueError(f"必须以 http:// 或 https:// 开头: {v}")
        return v.rstrip("/")

    @field_validator("code_execution_backend")
    @classmethod
    def normalize_code_execution_backend(cls, value: str) -> str:
        normalized = (value or "auto").strip().lower().replace("-", "_")
        allowed = {"auto", "e2b", "native", "docker", "disabled", "local_unsafe"}
        if normalized not in allowed:
            raise ValueError(f"Unsupported code execution backend: {value}")
        return normalized

    @field_validator("shell_execution_backend")
    @classmethod
    def normalize_shell_execution_backend(cls, value: str) -> str:
        normalized = (value or "auto").strip().lower().replace("-", "_")
        allowed = {"auto", "e2b", "native", "docker", "disabled", "local_unsafe"}
        if normalized not in allowed:
            raise ValueError(f"Unsupported shell execution backend: {value}")
        return normalized

    @property
    def llm(self) -> LLMSettings:
        return LLMSettings(
            api_key=self.llm_api_key,
            model_id=self.llm_model_id,
            base_url=self.llm_base_url,
            timeout=self.llm_timeout,
            model_tool_calling=self.model_tool_calling,
            model_json_mode=self.model_json_mode,
            model_thinking=self.model_thinking,
            model_thinking_budget=self.model_thinking_budget,
            judge_model_id=self.judge_model_id,
            judge_api_key=self.judge_api_key,
            judge_base_url=self.judge_base_url,
        )

    @property
    def rag(self) -> RAGSettings:
        return RAGSettings(
            enable_contextual_retrieval=self.enable_contextual_retrieval,
            enable_query_rewrite=self.enable_query_rewrite,
            enable_multi_query=self.enable_multi_query,
            enable_hyde=self.enable_hyde,
            hyde_question_only=self.hyde_question_only,
            enable_context_expansion=self.enable_context_expansion,
            multi_query_count=self.rag_multi_query_count,
            context_window=self.rag_context_window,
            context_max_chunks=self.rag_context_max_chunks,
            embedding_model=self.embedding_model,
            reranker_model=self.reranker_model,
            chroma_persist_dir=self.chroma_persist_dir,
            catalog_db_path=self.rag_catalog_db_path,
            default_namespace=self.rag_default_namespace,
            collection_prefix=self.rag_collection_prefix,
        )

    @property
    def memory(self) -> MemorySettings:
        return MemorySettings(
            db_path=self.memory_db_path,
            max_memories=self.max_memories,
            ttl_days=self.memory_ttl_days,
            autocompact_buffer_tokens=self.autocompact_buffer_tokens,
            large_result_threshold=self.large_result_threshold,
            offload_enabled=self.offload_enabled,
            snip_enabled=self.snip_enabled,
            time_microcompact_interval=self.time_microcompact_interval,
            post_compact_max_files=self.post_compact_max_files,
            post_compact_token_per_file=self.post_compact_token_per_file,
            post_compact_token_budget=self.post_compact_token_budget,
            transcript_enabled=self.transcript_enabled,
        )

    @property
    def mcp(self) -> MCPSettings:
        return MCPSettings(
            enabled=self.mcp_enabled,
            startup_timeout=self.mcp_startup_timeout,
            servers=self.mcp_servers,
        )

    @property
    def runtime(self) -> RuntimeSettings:
        return RuntimeSettings(
            max_agent_steps=self.max_agent_steps,
            traces_dir=self.traces_dir,
            trace_retention_days=self.trace_retention_days,
            shell_enabled=self.shell_enabled,
            shell_confirm=self.shell_confirm,
            shell_timeout=self.shell_timeout,
            code_execution_backend=self.code_execution_backend,
            code_execution_timeout=self.code_execution_timeout,
            code_execution_memory_mb=self.code_execution_memory_mb,
            code_execution_docker_image=self.code_execution_docker_image,
            code_execution_allow_unsafe_local=self.code_execution_allow_unsafe_local,
            shell_execution_backend=self.shell_execution_backend,
            shell_execution_memory_mb=self.shell_execution_memory_mb,
            shell_execution_docker_image=self.shell_execution_docker_image,
            file_read_max_mb=self.file_read_max_mb,
            shell_blacklist=self.shell_blacklist,
            runtime_profile=self.runtime_profile,
        )


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


def get_config_dir() -> Path:
    return _config_dir()


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


def write_config_yaml(data: dict) -> Path:
    return _write_yaml_config(data)


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


def load_config_yaml() -> dict:
    return _load_yaml()


_settings_cache: Settings | None = None


def get_settings() -> Settings:
    global _settings_cache
    if _settings_cache is None:
        data = _load_yaml()
        _settings_cache = Settings(**data, **_default_paths())
    return _settings_cache
