"""Config API routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(tags=["config"])


class ConfigUpdateRequest(BaseModel):
    key: str
    value: str


SETTABLE_KEYS = {
    "llm_api_key", "llm_model_id", "llm_base_url", "llm_timeout",
    "tavily_api_key", "e2b_api_key",
    "judge_api_key", "judge_model_id",
    "mcp_enabled",
    "max_agent_steps",
    "code_execution_backend", "code_execution_timeout",
    "code_execution_memory_mb", "code_execution_docker_image",
    "code_execution_allow_unsafe_local",
    "shell_execution_backend", "shell_execution_memory_mb",
    "shell_execution_docker_image",
    "enable_contextual_retrieval",
    "default_skill", "skill_auto_route",
    "skill_auto_route_llm_fallback",
    "skill_auto_route_min_score", "skill_auto_route_margin",
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
