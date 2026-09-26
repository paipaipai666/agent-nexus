import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from agentnexus.core.hook_schemas import HookConfig


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



class ModelOverride(BaseModel):
    """Per-model capability overrides. All-None = fully auto-detect."""

    context_length: int | None = Field(default=None, ge=1024)
    max_output_tokens: int | None = Field(default=None, ge=1)
    supports_vision: bool | None = None
    supports_tool_calling: bool | None = None
    supports_json_mode: bool | None = None
    supports_json_schema: bool | None = None
    supports_thinking: bool | None = None
    supports_parallel_tool_calls: bool | None = None
    # CNY per million tokens — optional cost estimate inputs
    input_price_cny_per_mtok: float | None = Field(default=None, ge=0)
    output_price_cny_per_mtok: float | None = Field(default=None, ge=0)


class ModelEntry(BaseModel):
    """One model offered by a provider, with optional capability overrides."""

    model_id: str
    override: ModelOverride | None = None


class LLMProvider(BaseModel):
    """A named, switchable LLM endpoint offering one or more models."""

    name: str
    models: list[ModelEntry] = Field(default_factory=list)
    base_url: str
    api_key: SecretStr = Field(default=SecretStr(""))
    timeout: int = Field(default=60, ge=1)

    @model_validator(mode="before")
    @classmethod
    def _legacy_model_id(cls, data: Any) -> Any:
        """旧 schema 的单 model_id 字段 → models 列表（一次性迁移）。"""
        if isinstance(data, dict) and "models" not in data and data.get("model_id"):
            data = dict(data)
            data["models"] = [{"model_id": data.pop("model_id")}]
        return data

    @field_validator("base_url")
    @classmethod
    def provider_base_url_must_have_scheme(cls, v: str) -> str:
        if v and not v.startswith(("http://", "https://")):
            raise ValueError("base_url 必须以 http:// 或 https:// 开头")
        return v


@dataclass
class ResolvedModel:
    """A concrete model choice: endpoint credentials + its registry entry."""

    model_id: str
    base_url: str
    api_key: SecretStr
    timeout: int
    provider: str
    entry: ModelEntry






class CapabilitiesSettings(BaseModel):
    """Settings for capabilities section of config."""

    model_config = {"extra": "allow"}

    mcp_servers: dict[str, Any] = Field(default_factory=dict)
    plugins: dict[str, Any] = Field(default_factory=dict)
    skills: dict[str, Any] = Field(default_factory=dict)
    tools: dict[str, Any] = Field(default_factory=dict)
    states: dict[str, Any] = Field(default_factory=dict)


class PersonaProject(BaseModel):
    """A single project entry in the persona mission map."""

    name: str
    focus: str = "进行中"


class PersonaConfig(BaseModel):
    """User-defined persona configuration for the agent.

    Loaded from the ``persona`` section of ``config.yaml``.
    Compiled into a prompt fragment at agent initialization.
    """

    agent_name: str = ""
    identity: str = ""
    tone: str = ""
    projects: list[PersonaProject] = Field(default_factory=list)





class Settings(BaseSettings):
    """Application-wide settings loaded from config.yaml + environment variables.

    Flat fields grouped under comment headers for navigation. Typed views
    remain only where consumers need them: ``.capabilities`` and ``.persona``.
    """

    model_config = SettingsConfigDict(env_prefix="AGENTNEXUS_", extra="ignore")

    def __init__(self, **data: Any):
        capabilities = data.pop("capabilities", None)
        persona = data.pop("persona", None)
        super().__init__(**data)
        self._migrate_llm_profiles()
        self._raw_capabilities: dict[str, Any] = capabilities if isinstance(capabilities, dict) else {}
        self._raw_persona: dict[str, Any] = persona if isinstance(persona, dict) else {}

    # ── LLM / Model Configuration ────────────────────────────────────────
    # No vendor defaults — fill these (or llm_providers) before calling the model.
    llm_api_key: SecretStr = Field(default=SecretStr(""))
    llm_model_id: str = Field(default="")
    llm_base_url: str = Field(default="")
    llm_timeout: int = Field(default=60, ge=1)
    # Model capability overrides (None = auto-detect)
    model_tool_calling: bool | None = Field(default=None)
    model_json_mode: bool | None = Field(default=None)
    model_thinking: bool | None = Field(default=None)
    # Thinking depth: maps to OpenAI reasoning_effort / Anthropic budget / Gemini thinkingLevel.
    # Vendor token budgets are derived internally — not user-facing.
    model_thinking_effort: str = Field(default="medium")
    # Judge LLM (used by evaluators) — empty = follow the task model
    judge_model_id: str = Field(default="")
    judge_api_key: SecretStr = Field(default=SecretStr(""))
    judge_base_url: str = Field(default="")
    # Switchable provider profiles. When active_model/active_provider matches
    # an entry here, it overrides the flat llm_* fields (default/fallback).
    llm_providers: list[LLMProvider] = Field(default_factory=list)
    active_provider: str = Field(default="")  # legacy provider-granularity selector
    active_model: str = Field(default="")  # "provider/model"; "" = fall back
    judge_model: str = Field(default="")  # "provider/model"; "" = legacy judge_* 或跟随任务模型

    # ── External Service Keys ─────────────────────────────────────────────
    tavily_api_key: SecretStr = Field(default=SecretStr(""))
    e2b_api_key: SecretStr = Field(default=SecretStr(""))

    # ── Agent Runtime ─────────────────────────────────────────────────────
    max_agent_steps: int = Field(default=50, ge=1, le=200)
    subagent_max_concurrent: int = Field(default=3, ge=1, le=8)
    # 用户自定义追加指令：注入系统上下文末尾，优先级高于平台默认行为
    # 准则，但不得覆盖安全约束。支持多行文本。
    append_system_prompt: str = Field(default="")

    # ── Command Hooks ─────────────────────────────────────────────────────
    # 声明式命令钩子（见 docs/Hooks.md）。config.yaml 内的 hooks 视为
    # 用户级、默认信任；~/.agentnexus/hooks.yaml 同；项目级
    # <workspace>/.agentnexus/hooks.yaml 需经 hash 信任审查。
    hooks: list[HookConfig] = Field(default_factory=list)
    hook_trust: str = Field(default="strict")  # strict | bypass
    hooks_enabled: bool = Field(default=True)
    # 默认 false：permission_request 钩子只能 deny，不得自动放行 HITL。
    # 显式开启后钩子决策仍写入 audit（hitl_decision=hook_allowed）。
    hitl_hooks_may_approve: bool = Field(default=False)
    # fire() 按 PAYLOAD_SCHEMAS 校验 payload 键类型，发现 fire 点漂移即报错。
    hook_schema_check: bool = Field(default=True)
    # 每次 fire 的逐钩子结果追加到 {traces_dir}/hooks.jsonl（审计/排障）。
    hooks_journal: bool = Field(default=True)
    # 允许插件执行代码入口（plugin.yaml entrypoint: hooks.py）。默认 false：
    # 声明式插件保持零代码；代码插件是显式 opt-in。
    plugins_allow_code: bool = Field(default=False)

    # ── RAG / Retrieval ──────────────────────────────────────────────────
    enable_contextual_retrieval: bool = Field(default=False)
    # express_reaction 工具（娱乐：模型自主决定是否对用户提问发表情反应）。
    # 默认 false：不往工具列表里注入娱乐工具；开启后模型可选择调用。
    enable_user_reaction: bool = Field(default=True)
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
    rag_catalog_db_path: str = Field(default="")
    rag_default_namespace: str = Field(default="default")
    rag_collection_prefix: str = Field(default="kb_")

    # ── Storage Paths ─────────────────────────────────────────────────────
    memory_db_path: str = Field(default="")
    traces_dir: str = Field(default="")

    # ── Memory System ─────────────────────────────────────────────────────
    max_memories: int = Field(default=1000, ge=100, le=100000)
    memory_ttl_days: int = Field(default=90, ge=7, le=365)
    # Whitelist gate: only strong-signal content is extracted. When False,
    # borderline cases are dropped (0 token); when True, an LLM gate judges them.
    memory_llm_gate: bool = Field(default=False)
    trace_retention_days: int = Field(default=30, ge=1, le=365)

    # ── MCP (Model Context Protocol) ──────────────────────────────────────
    mcp_enabled: bool = Field(default=False)
    mcp_startup_timeout: int = Field(default=15, ge=1, le=300)
    mcp_servers: list[MCPServerConfig] = Field(default_factory=list)
    # Compaction tuning
    autocompact_buffer_tokens: int = Field(default=8000, ge=1000, le=100000)
    # 分段索引压缩：每段消息数（越小索引越细，成本越高）
    memory_index_segment_size: int = Field(default=20, ge=5, le=100)
    # 索引总 token 预算：超过后最老条目折叠为归档目录，细节靠 history_search 检索
    memory_index_max_tokens: int = Field(default=4000, ge=500, le=100000)
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
    # Skill catalog block budget. Dual bound from production/research:
    # - ratio: soft share of the model context window (tool/skill static prefix
    #   guidance is ~1-5% of window; larger windows should share a smaller slice)
    # - max_tokens: hard ceiling (~Claude Code available_skills ~16k chars ≈ 4k
    #   tokens, and ~100 tokens/skill metadata per Anthropic Agent Skills docs)
    # budget = clamp(window * ratio, 160, max_tokens)
    skill_context_token_ratio: float = Field(default=0.02, ge=0.001, le=0.2)
    skill_context_max_tokens: int = Field(default=4000, ge=200, le=50000)
    runtime_profile: str = Field(default="default")
    # 预算分层配置
    budget_simple_max_tokens: int = Field(default=5000, ge=1000, le=100000)
    budget_complex_max_tokens: int = Field(default=50000, ge=5000, le=500000)
    budget_high_value_max_tokens: int = Field(default=200000, ge=10000, le=2000000)
    budget_exceed_strategy: str = Field(default="compress")
    # 浏览器自动化配置
    browser_mode: str = Field(default="isolated", description="浏览器模式: isolated=无状态新浏览器, cdp=连接用户浏览器")
    browser_cdp_endpoint: str = Field(default="http://localhost:9222", description="CDP连接地址(mode=cdp时使用)")
    browser_headless: bool = Field(default=False, description="无头模式(仅isolated模式生效,默认有头)")
    browser_viewport_width: int = Field(default=1280, ge=320, le=3840)
    browser_viewport_height: int = Field(default=720, ge=240, le=2160)
    browser_default_timeout: int = Field(default=30000, ge=1000, le=120000, description="Playwright操作超时(ms)")
    browser_networkidle_timeout: int = Field(default=5000, ge=1000, le=30000, description="networkidle独立超时(ms)")
    browser_screenshot_dir: str = Field(default="", description="截图保存目录")
    browser_context_ttl: int = Field(default=600, ge=60, le=3600, description="per-task context无操作自动回收时间(秒)")
    browser_allow_js_execution: bool = Field(default=False, description="是否允许执行JavaScript(默认禁用)")
    browser_snapshot_max_nodes: int = Field(default=100, ge=10, le=1000, description="snapshot最大节点数")
    browser_hitl_rules: list[dict[str, str]] = Field(
        default_factory=list,
        description="HITL触发规则列表，格式: [{action:'click', role:'button', name_pattern:'支付|确认'}]",
    )
    # 桌面自动化配置
    computer_use_enabled: bool = Field(default=False, description="是否启用桌面自动化功能")
    computer_use_backend: str = Field(default="auto", description="后端: auto/windows/linux/macos")
    computer_use_snapshot_max_nodes: int = Field(default=100, ge=10, le=1000, description="snapshot最大节点数")
    computer_use_hitl_rules: list[dict[str, str]] = Field(
        default_factory=list,
        description="HITL触发规则列表，格式: [{action:'click', role:'button', name_pattern:'支付|确认'}]",
    )
    computer_use_allowed_apps: list[str] = Field(
        default_factory=list,
        description="允许操控的应用白名单（空=全部允许）",
    )
    computer_use_blocked_apps: list[str] = Field(
        default_factory=lambda: ["taskmgr", "regedit", "cmd", "powershell", "terminal"],
        description="禁止操控的应用黑名单",
    )
    # ── Wiki 系统 ─────────────────────────────────────────────────
    wiki_enabled: bool = Field(default=False, description="启用混合 Wiki + RAG 知识系统")
    wiki_namespace: str = Field(default="wiki", description="Wiki 页面的 ChromaDB 命名空间")
    wiki_review_sla_p1_days: int = Field(default=7, ge=1, le=90, description="P1 review 期限（天）")
    wiki_review_sla_p2_days: int = Field(default=14, ge=1, le=90, description="P2 review 期限（天）")
    wiki_review_sla_p3_days: int = Field(default=30, ge=1, le=180, description="P3 review 期限（天）")
    wiki_propagation_max_depth: int = Field(default=3, ge=1, le=10, description="置信度传播最大深度")
    wiki_calibration_retrigger_pct: float = Field(default=0.5, ge=0.1, le=2.0, description="wiki 规模增长多少比例后需要重新校准")
    # 机械验证阈值（上线前通过校准调整）
    wiki_jaccard_direct_quote: float = Field(default=0.6, ge=0.0, le=1.0, description="Jaccard 相似度 > 此值判定为 direct_quote")
    wiki_jaccard_paraphrase: float = Field(default=0.4, ge=0.0, le=1.0, description="Jaccard 相似度 > 此值判定为 paraphrase")
    wiki_cosine_paraphrase: float = Field(default=0.7, ge=0.0, le=1.0, description="余弦相似度 > 此值确认 paraphrase")
    wiki_cosine_source: float = Field(default=0.35, ge=0.0, le=1.0, description="余弦相似度 >= 此值认为 chunk 是有效来源")
    wiki_drift_threshold: float = Field(default=0.5, ge=0.0, le=1.0, description="canonical_definition 偏离度阈值")

    @field_validator("llm_base_url", "judge_base_url")
    @classmethod
    def must_have_scheme(cls, v: str) -> str:
        if v and not v.startswith(("http://", "https://")):
            raise ValueError(f"必须以 http:// 或 https:// 开头: {v}")
        return v.rstrip("/")

    @field_validator("browser_mode")
    @classmethod
    def normalize_browser_mode(cls, value: str) -> str:
        normalized = (value or "isolated").strip().lower()
        if normalized not in {"isolated", "cdp"}:
            raise ValueError(f"不支持的浏览器模式: {value}，可选: isolated, cdp")
        return normalized

    @field_validator("model_thinking_effort")
    @classmethod
    def normalize_thinking_effort(cls, value: str) -> str:
        normalized = (value or "medium").strip().lower()
        if normalized not in {"none", "low", "medium", "high"}:
            raise ValueError(f"不支持的思考强度: {value}，可选: none, low, medium, high")
        return normalized

    @field_validator("computer_use_backend")
    @classmethod
    def normalize_computer_use_backend(cls, value: str) -> str:
        normalized = (value or "auto").strip().lower()
        if normalized not in {"auto", "windows", "linux", "macos"}:
            raise ValueError(f"不支持的桌面自动化后端: {value}，可选: auto, windows, linux, macos")
        return normalized

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

    def get_active_llm_profile(self) -> tuple[str, str, SecretStr, int]:
        """Resolve (model_id, base_url, api_key, timeout) of the active model.

        Priority: active_model selector > legacy active_provider (first model)
        > flat ``llm_*`` fields.
        """
        resolved = self.resolve_active_model()
        if resolved is not None:
            return resolved.model_id, resolved.base_url, resolved.api_key, resolved.timeout
        return self.llm_model_id, self.llm_base_url, self.llm_api_key, self.llm_timeout

    def resolve_active_model(self) -> "ResolvedModel | None":
        """Resolve the active_model/active_provider selector, or None."""
        if self.active_model:
            found = self.find_model(self.active_model)
            if found is not None:
                provider, entry = found
                return ResolvedModel(
                    model_id=entry.model_id, base_url=provider.base_url,
                    api_key=provider.api_key, timeout=provider.timeout,
                    provider=provider.name, entry=entry,
                )
        if self.active_provider:
            for p in self.llm_providers:
                if p.name == self.active_provider and p.models:
                    return ResolvedModel(
                        model_id=p.models[0].model_id, base_url=p.base_url,
                        api_key=p.api_key, timeout=p.timeout,
                        provider=p.name, entry=p.models[0],
                    )
        return None

    def find_model(self, selector: str) -> "tuple[LLMProvider, ModelEntry] | None":
        """Find (provider, entry) for a "provider/model_id" selector."""
        pname, sep, mid = selector.partition("/")
        if not sep or not pname or not mid:
            return None
        for p in self.llm_providers:
            if p.name != pname:
                continue
            for m in p.models:
                if m.model_id == mid:
                    return p, m
        return None

    def find_model_override_entry(self, model_id: str, base_url: str) -> "ModelEntry | None":
        """Locate the ModelEntry for an endpoint — feeds per-model capability overrides."""
        target = (base_url or "").rstrip("/").lower()
        for p in self.llm_providers:
            if (p.base_url or "").rstrip("/").lower() != target:
                continue
            for m in p.models:
                if m.model_id == model_id:
                    return m
        return None

    def get_judge_profile(self) -> tuple[str, str, SecretStr, int]:
        """Resolve judge model: explicit selector > legacy judge_* > follow task model."""
        if self.judge_model:
            found = self.find_model(self.judge_model)
            if found is not None:
                provider, entry = found
                return entry.model_id, provider.base_url, provider.api_key, provider.timeout
        if self._judge_legacy_configured():
            key = self.judge_api_key.get_secret_value() or self.llm_api_key.get_secret_value()
            return self.judge_model_id, self.judge_base_url, SecretStr(key), self.llm_timeout
        resolved = self.resolve_active_model()
        if resolved is not None:
            return resolved.model_id, resolved.base_url, resolved.api_key, resolved.timeout
        return self.llm_model_id, self.llm_base_url, self.llm_api_key, self.llm_timeout

    def _judge_legacy_configured(self) -> bool:
        """True when judge_* flat fields were explicitly set (any non-empty value)."""
        return bool(self.judge_api_key.get_secret_value()) \
            or bool(self.judge_model_id.strip()) \
            or bool((self.judge_base_url or "").strip())

    def _migrate_llm_profiles(self) -> None:
        """Idempotent in-memory migration toward the provider/models schema.

        Persists on the next settings save; never writes to disk itself.
        """
        providers = list(self.llm_providers)
        # flat llm_* → seed a "default" provider so pickers have an entry and
        # switching models becomes discoverable without touching config.yaml.
        if not providers and self.llm_model_id:
            providers = [LLMProvider(
                name="default",
                models=[ModelEntry(model_id=self.llm_model_id)],
                base_url=self.llm_base_url,
                api_key=self.llm_api_key,
                timeout=self.llm_timeout,
            )]
            self.llm_providers = providers
        # legacy provider-granularity selector → model-granularity
        if not self.active_model and self.active_provider:
            for p in providers:
                if p.name == self.active_provider and p.models:
                    self.active_model = f"{p.name}/{p.models[0].model_id}"
                    break
        # freshly seeded default provider becomes active immediately
        if not self.active_model and len(providers) == 1 \
                and providers[0].name == "default" and providers[0].models:
            self.active_model = f"default/{providers[0].models[0].model_id}"

    @property
    def capabilities(self) -> CapabilitiesSettings:
        """Return typed capabilities settings."""
        raw = getattr(self, "_raw_capabilities", None) or {}
        return CapabilitiesSettings(**raw)

    @property
    def persona(self) -> PersonaConfig:
        """Return typed persona settings."""
        raw = getattr(self, "_raw_persona", None) or {}
        return PersonaConfig(**raw)


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
