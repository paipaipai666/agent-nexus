"""Runtime status API routes — model, context, tokens."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query

router = APIRouter(tags=["runtime"])


def _read_db_stats(runtime, session_id: str) -> Optional[dict]:
    """Read persisted session stats from the database as a fallback."""
    try:
        from agentnexus.memory.versioned import ConversationVersionManager
        # Try runtime first (testable), fall back to settings
        db_path = getattr(runtime, "_db_path", None)
        if not db_path:
            from agentnexus.core.config import get_settings
            db_path = get_settings().memory_db_path
        if not db_path:
            return None
        return ConversationVersionManager.get_session_stats(db_path, session_id)
    except Exception:
        return None


def _resolve_session_refs(runtime, session_id: Optional[str]):
    """Return (agent, memory_manager) for the given session.

    Looks up per-session instances from ChatService when *session_id* is
    provided, falling back to the build-time agent/memory on the runtime
    itself (which always have zero stats — they never run queries).
    """
    agent = runtime.agent
    mm = runtime.memory_manager

    if session_id:
        chat = getattr(runtime.services, "chat", None)
        if chat:
            per_session_agent = getattr(chat, "_agents", {}).get(session_id)
            per_session_mm = getattr(chat, "_memory_managers", {}).get(session_id)
            if per_session_agent is not None:
                agent = per_session_agent
            if per_session_mm is not None:
                mm = per_session_mm

    return agent, mm


@router.get("/status")
def runtime_status(session_id: Optional[str] = Query(None, description="Session ID for per-session stats")):
    from agentnexus.server.app import _get_runtime

    runtime = _get_runtime()
    settings = runtime.settings

    agent, mm = _resolve_session_refs(runtime, session_id)

    # Model info
    model_id = getattr(agent, "model_id", None) or getattr(settings, "llm_model_id", "unknown")

    # Token usage — prefer in-memory agent stats; fall back to DB for historical sessions
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

    # Agent step count — prefer in-memory; fall back to DB
    step_count = 0
    if agent and hasattr(agent, "_step_count"):
        step_count = agent._step_count

    # DB fallback: when no per-session agent exists, read persisted stats
    if session_id and agent is runtime.agent:
        db_stats = _read_db_stats(runtime, session_id)
        if db_stats:
            usage = {"input_tokens": db_stats["input_tokens"], "output_tokens": db_stats["output_tokens"]}
            step_count = db_stats["step_count"]

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
