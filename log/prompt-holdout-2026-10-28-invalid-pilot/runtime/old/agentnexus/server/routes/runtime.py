"""Runtime status API routes — model, context, tokens."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from agentnexus.agents.context_usage import ContextUsageSnapshot
from agentnexus.server.deps import get_runtime

router = APIRouter(tags=["runtime"])


def _read_db_stats(runtime, session_id: str) -> dict | None:
    """Read persisted session stats from the database as a fallback."""
    try:
        from agentnexus.memory.versioned import ConversationVersionManager
        db_path = getattr(runtime, "_db_path", None)
        if not db_path:
            from agentnexus.core.config import get_settings
            db_path = get_settings().memory_db_path
        if not db_path:
            return None
        return ConversationVersionManager.get_session_stats(db_path, session_id)
    except Exception:
        return None


def _resolve_session_refs(runtime, session_id: str | None):
    """Return (agent, memory_manager) for the given session.

    Looks up per-session instances from ChatService when *session_id* is
    provided, falling back to the build-time agent/memory on the runtime
    itself (which always have zero stats — they never run queries).
    """
    agent = runtime.agent
    mm = runtime.memory_manager

    if session_id:
        chat = runtime.chat
        if chat:
            per_session_agent = getattr(chat, "_agents", {}).get(session_id)
            per_session_mm = getattr(chat, "_memory_managers", {}).get(session_id)
            if per_session_agent is not None:
                agent = per_session_agent
            if per_session_mm is not None:
                mm = per_session_mm

    return agent, mm


@router.get("/status")
def runtime_status(session_id: str | None = Query(None, description="Session ID for per-session stats"), runtime=Depends(get_runtime)):
    settings = runtime.settings

    agent, mm = _resolve_session_refs(runtime, session_id)

    model_id = getattr(agent, "model_id", None) or settings.get_active_llm_profile()[0]

    usage = {}
    if agent and hasattr(agent, "total_usage"):
        usage = agent.total_usage

    ctx_max = 128000
    resolved = getattr(mm, "ctx_max", None) if mm is not None else None
    if resolved:
        ctx_max = resolved
    elif hasattr(settings, "max_context_tokens"):
        ctx_max = settings.max_context_tokens

    stm_tokens = 0
    if mm and hasattr(mm, "estimate_stm_tokens"):
        try:
            stm_tokens = mm.estimate_stm_tokens()
        except Exception:
            pass

    step_count = 0
    if agent and hasattr(agent, "_step_count"):
        step_count = agent._step_count

    if session_id and agent is runtime.agent:
        db_stats = _read_db_stats(runtime, session_id)
        if db_stats:
            usage = {"input_tokens": db_stats["input_tokens"], "output_tokens": db_stats["output_tokens"]}
            step_count = db_stats["step_count"]

    skill_id = None
    skill_service = runtime.skill
    if skill_service:
        snapshot = skill_service.snapshot() if hasattr(skill_service, "snapshot") else {}
        skill_id = getattr(snapshot, "current_skill_id", None) or (snapshot.get("current_skill_id") if isinstance(snapshot, dict) else None)

    session_loaded = not session_id or session_id in getattr(runtime.chat, "_agents", {})
    context_snapshot = getattr(agent, "context_usage", None) if session_loaded else None
    context_usage = context_snapshot.to_dict(capacity=ctx_max) if isinstance(context_snapshot, ContextUsageSnapshot) else None
    threshold = getattr(mm, "effective_compact_threshold", None) or getattr(mm, "compact_threshold", None)
    compaction = None
    if session_loaded and isinstance(threshold, int):
        compaction = {"basis": "short_term_memory", "used_tokens": stm_tokens, "threshold_tokens": threshold}

    return {
        "model_id": model_id,
        "total_usage": usage,
        "ctx_max": ctx_max,
        "stm_tokens": stm_tokens,
        "step_count": step_count,
        "skill_id": skill_id,
        "context_usage": context_usage,
        "compaction": compaction,
    }


def _schedule_process_exit(delay_sec: float = 1.0) -> None:
    """Exit the process shortly after the response flushes.

    The server runs as the desktop wrapper's sidecar (bound to loopback);
    uvicorn's SIGTERM path can't be reached from an HTTP handler on Windows
    (os.kill(SIGTERM) is a hard kill there), so exit is scheduled directly.
    Runs were already cancelled + persisted above.
    """
    import os
    import threading

    timer = threading.Timer(delay_sec, os._exit, args=(0,))
    timer.daemon = True
    timer.start()


@router.post("/shutdown")
def shutdown(runtime=Depends(get_runtime)):
    """Graceful shutdown for the desktop wrapper.

    决策 1/6: closing the app must stop everything — cancel all active runs
    (results persist, terminal events queue) and then exit the process.
    """
    chat = getattr(getattr(runtime, "services", None), "chat", None)
    if chat is not None and hasattr(chat, "cancel_all_runs"):
        chat.cancel_all_runs(reason="server_shutdown")
    _schedule_process_exit()
    return {"status": "shutting_down"}
