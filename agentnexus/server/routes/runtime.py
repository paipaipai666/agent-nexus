"""Runtime status API routes — model, context, tokens."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["runtime"])


@router.get("/status")
def runtime_status():
    from agentnexus.server.app import _get_runtime

    runtime = _get_runtime()
    agent = runtime.agent
    mm = runtime.memory_manager
    settings = runtime.settings

    # Model info
    model_id = getattr(agent, "model_id", None) or getattr(settings, "llm_model_id", "unknown")

    # Token usage
    usage = {}
    if agent and hasattr(agent, "total_usage"):
        usage = agent.total_usage

    # Context window
    ctx_max = 128000
    if mm and hasattr(mm, "_ctx_max"):
        ctx_max = mm._ctx_max
    elif hasattr(settings, "max_context_tokens"):
        ctx_max = settings.max_context_tokens

    # Current STM tokens
    stm_tokens = 0
    if mm and hasattr(mm, "estimate_stm_tokens"):
        try:
            stm_tokens = mm.estimate_stm_tokens()
        except Exception:
            pass

    # Agent step count
    step_count = 0
    if agent and hasattr(agent, "_step_count"):
        step_count = agent._step_count

    # Skill info
    skill_id = None
    skill_service = getattr(runtime.services, "skill", None)
    if skill_service:
        snapshot = skill_service.snapshot() if hasattr(skill_service, "snapshot") else {}
        skill_id = getattr(snapshot, "current_skill_id", None) or (snapshot.get("current_skill_id") if isinstance(snapshot, dict) else None)

    return {
        "model_id": model_id,
        "total_usage": usage,
        "ctx_max": ctx_max,
        "stm_tokens": stm_tokens,
        "step_count": step_count,
        "skill_id": skill_id,
    }
