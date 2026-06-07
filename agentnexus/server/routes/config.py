"""Config API routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(tags=["config"])


class ConfigUpdateRequest(BaseModel):
    key: str
    value: str


SETTABLE_KEYS = {
    # LLM
    "llm_api_key", "llm_model_id", "llm_base_url", "llm_timeout",
    "model_tool_calling", "model_json_mode", "model_thinking", "model_thinking_budget",
    # Judge LLM
    "judge_api_key", "judge_model_id", "judge_base_url",
    # Agent
    "max_agent_steps",
    # External Services
    "tavily_api_key", "e2b_api_key",
    # RAG
    "enable_contextual_retrieval", "enable_query_rewrite", "enable_multi_query",
    "enable_hyde", "hyde_question_only", "enable_context_expansion",
    "rag_multi_query_count", "rag_context_window", "rag_context_max_chunks",
    "embedding_model", "reranker_model", "rag_default_namespace", "rag_collection_prefix",
    # Memory
    "max_memories", "memory_ttl_days",
    "autocompact_buffer_tokens", "large_result_threshold",
    "offload_enabled", "snip_enabled", "time_microcompact_interval",
    "post_compact_max_files", "post_compact_token_per_file", "post_compact_token_budget",
    "transcript_enabled",
    # MCP
    "mcp_enabled", "mcp_startup_timeout",
    # Code Execution
    "code_execution_backend", "code_execution_timeout",
    "code_execution_memory_mb", "code_execution_docker_image",
    "code_execution_allow_unsafe_local",
    # Shell Execution
    "shell_enabled", "shell_confirm", "shell_timeout",
    "shell_execution_backend", "shell_execution_memory_mb",
    "shell_execution_docker_image", "shell_blacklist",
    # File Operations
    "file_read_max_mb",
    # Extensions & Skills
    "extensions_enabled", "extensions_dirs", "plugins_auto_discover",
    "skills_default_namespace", "default_skill",
    "skill_auto_route", "skill_auto_route_llm_fallback",
    "skill_auto_route_min_score", "skill_auto_route_margin",
    # Runtime
    "runtime_profile",
    "trace_retention_days",
    # Budget
    "budget_simple_max_tokens", "budget_complex_max_tokens",
    "budget_high_value_max_tokens", "budget_exceed_strategy",
    # Browser Automation
    "browser_mode", "browser_cdp_endpoint", "browser_headless",
    "browser_viewport_width", "browser_viewport_height",
    "browser_default_timeout", "browser_networkidle_timeout",
    "browser_screenshot_dir", "browser_context_ttl",
    "browser_allow_js_execution", "browser_snapshot_max_nodes",
    # Desktop Automation
    "computer_use_enabled", "computer_use_backend",
    "computer_use_snapshot_max_nodes",
    "computer_use_allowed_apps", "computer_use_blocked_apps",
}


@router.get("")
def get_config():
    from agentnexus.core.config import get_settings

    settings = get_settings()
    config = {}
    for name in type(settings).model_fields:
        value = getattr(settings, name)
        if hasattr(value, "get_secret_value"):
            value = "****"
        config[name] = value
    return config


@router.put("")
def update_config(req: ConfigUpdateRequest):
    from agentnexus.core.config import load_config_yaml, write_config_yaml

    if req.key not in SETTABLE_KEYS:
        raise HTTPException(status_code=400, detail=f"Key '{req.key}' is not settable")

    data = load_config_yaml()
    data[req.key] = req.value
    write_config_yaml(data)

    import agentnexus.core.config as cfg
    if hasattr(cfg, "_settings_cache"):
        cfg._settings_cache = None

    return {"status": "updated", "key": req.key}


@router.get("/extensions")
def get_extensions():
    from agentnexus.server.app import _get_runtime

    runtime = _get_runtime()
    em = runtime.extension_manager
    if em is None:
        return {"extensions": [], "count": 0}
    status = em.status() if hasattr(em, "status") else {}
    return status if isinstance(status, dict) else {"status": str(status)}
