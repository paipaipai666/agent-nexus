"""Config API routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(tags=["config"])


class ConfigUpdateRequest(BaseModel):
    key: str
    value: str

class PersonaProjectUpdate(BaseModel):
    name: str
    focus: str = "进行中"


class PersonaUpdateRequest(BaseModel):
    agent_name: str = ""
    identity: str = ""
    tone: str = ""
    projects: list[PersonaProjectUpdate] = []


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
    # Shell Execution
    "shell_enabled", "shell_confirm", "shell_timeout",
    "shell_execution_backend", "shell_execution_memory_mb",
    "shell_execution_docker_image",
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

# Security-sensitive keys that cannot be modified via the API.
# These can weaken sandboxing, bypass safety checks, or alter audit behavior.
_SECURITY_BLOCKED_KEYS = {
    "code_execution_allow_unsafe_local",
    "shell_blacklist",
}


@router.get("")
def get_config():
    from agentnexus.core.config import get_settings

    settings = get_settings()
    config: dict[str, Any] = {}
    for name in type(settings).model_fields:
        value = getattr(settings, name)
        if hasattr(value, "get_secret_value"):
            value = "****"
        config[name] = value
    # Include persona as nested object (not a model_field, loaded from raw yaml)
    persona = settings.persona
    config["persona"] = {
        "agent_name": persona.agent_name,
        "identity": persona.identity,
        "tone": persona.tone,
        "projects": [{"name": p.name, "focus": p.focus} for p in persona.projects],
    }
    # Workspace is the process cwd, not a Settings field — expose it so the
    # desktop status bar can display the real value. Sessions carry their own
    # workspace folders (Codex-style) — there is no global workspace switch.
    from pathlib import Path
    config["cwd"] = str(Path.cwd())
    # Provider api keys need explicit masking (the loop above only masks
    # top-level SecretStr fields).
    config["llm_providers"] = [_mask_provider(p) for p in settings.llm_providers]
    return config


class ProviderInput(BaseModel):
    name: str
    model_id: str
    base_url: str
    api_key: str | None = None  # None or "****" = keep the stored key
    timeout: int = 60


class ProvidersUpdateRequest(BaseModel):
    providers: list[ProviderInput]


class ActiveProviderRequest(BaseModel):
    name: str  # "" = legacy flat llm_* config


def _mask_provider(p: Any) -> dict[str, Any]:
    return {
        "name": p.name,
        "model_id": p.model_id,
        "base_url": p.base_url,
        "api_key": "****" if p.api_key.get_secret_value() else "",
        "timeout": p.timeout,
    }


def _validate_providers(providers: list[ProviderInput]) -> list[dict[str, Any]]:
    """Validate/normalize provider entries. Raises ValueError on bad input."""
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for p in providers:
        name = p.name.strip()
        if not name:
            raise ValueError("provider name is required")
        if name in seen:
            raise ValueError(f"duplicate provider name: {name}")
        seen.add(name)
        if not p.model_id.strip():
            raise ValueError(f"provider '{name}': model_id is required")
        if not p.base_url.startswith(("http://", "https://")):
            raise ValueError(f"provider '{name}': base_url must start with http(s)://")
        out.append({
            "name": name,
            "model_id": p.model_id.strip(),
            "base_url": p.base_url.strip(),
            "api_key": p.api_key,
            "timeout": p.timeout,
        })
    return out


def _apply_active_llm(runtime: Any, settings: Any) -> None:
    """Push the active provider profile into the shared LLM client (live switch —
    every per-session agent holds this same instance)."""
    model_id, base_url, api_key, timeout = settings.get_active_llm_profile()
    llm = getattr(runtime, "llm", None)
    if llm is not None and hasattr(llm, "configure"):
        llm.configure(model=model_id, base_url=base_url, api_key=api_key.get_secret_value(), timeout=timeout)


def _reset_settings_cache() -> None:
    import agentnexus.core.config as cfg
    if hasattr(cfg, "_settings_cache"):
        cfg._settings_cache = None


@router.get("/llm/providers")
def list_llm_providers():
    from agentnexus.core.config import get_settings

    settings = get_settings()
    return {
        "providers": [_mask_provider(p) for p in settings.llm_providers],
        "active": settings.active_provider,
        "legacy": {
            "model_id": settings.llm_model_id,
            "base_url": settings.llm_base_url,
            "has_api_key": bool(settings.llm_api_key.get_secret_value()),
        },
    }


@router.put("/llm/providers")
def update_llm_providers(req: ProvidersUpdateRequest):
    from agentnexus.core.config import get_settings, load_config_yaml, write_config_yaml
    from agentnexus.server.app import _get_runtime

    try:
        validated = _validate_providers(req.providers)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    data = load_config_yaml()
    stored = {p.get("name"): p for p in (data.get("llm_providers") or []) if isinstance(p, dict)}
    for entry in validated:
        if not entry["api_key"] or entry["api_key"] == "****":  # unset/masked = keep stored key
            entry["api_key"] = (stored.get(entry["name"]) or {}).get("api_key", "")
    data["llm_providers"] = validated
    write_config_yaml(data)
    _reset_settings_cache()

    # Active provider may have been edited or removed — re-resolve live.
    _apply_active_llm(_get_runtime(), get_settings())
    return {"status": "updated", "count": len(validated)}


@router.post("/llm/active")
def set_active_llm_provider(req: ActiveProviderRequest):
    from agentnexus.core.config import get_settings, load_config_yaml, write_config_yaml
    from agentnexus.server.app import _get_runtime

    data = load_config_yaml()
    stored_names = {p.get("name") for p in (data.get("llm_providers") or []) if isinstance(p, dict)}
    if req.name and req.name not in stored_names:
        raise HTTPException(status_code=404, detail=f"Unknown provider: {req.name}")
    data["active_provider"] = req.name
    write_config_yaml(data)
    _reset_settings_cache()

    settings = get_settings()
    _apply_active_llm(_get_runtime(), settings)
    return {"status": "updated", "active": req.name, "model_id": settings.get_active_llm_profile()[0]}

@router.put("")
def update_config(req: ConfigUpdateRequest):
    from agentnexus.core.config import load_config_yaml, write_config_yaml

    if req.key not in SETTABLE_KEYS:
        raise HTTPException(status_code=400, detail=f"Key '{req.key}' is not settable")

    if req.key in _SECURITY_BLOCKED_KEYS:
        raise HTTPException(
            status_code=403,
            detail=f"Key '{req.key}' is security-sensitive and cannot be modified via API",
        )

    data = load_config_yaml()
    data[req.key] = req.value
    write_config_yaml(data)

    _reset_settings_cache()

    return {"status": "updated", "key": req.key}


@router.put("/persona")
def update_persona(req: PersonaUpdateRequest):
    from agentnexus.core.config import load_config_yaml, write_config_yaml

    data = load_config_yaml()
    persona_data: dict[str, Any] = {
        "agent_name": req.agent_name,
        "identity": req.identity,
        "tone": req.tone,
        "projects": [{"name": p.name, "focus": p.focus} for p in req.projects],
    }
    data["persona"] = persona_data
    write_config_yaml(data)

    _reset_settings_cache()

    return {"status": "updated", "persona": persona_data}


@router.get("/extensions")
def get_extensions():
    from agentnexus.server.app import _get_runtime

    runtime = _get_runtime()
    em = runtime.extension_manager
    if em is None:
        return {"extensions": [], "count": 0}
    status = em.status() if hasattr(em, "status") else {}
    return status if isinstance(status, dict) else {"status": str(status)}
