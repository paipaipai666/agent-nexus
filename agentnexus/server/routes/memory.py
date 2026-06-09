"""Memory API routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(tags=["memory"])


class SearchMemoryRequest(BaseModel):
    query: str
    limit: int = 5


@router.get("/list")
def list_memories(limit: int = 20):
    from agentnexus.memory.long_term import get_long_term_memory

    ltm = get_long_term_memory()
    memories = ltm.list_recent(limit)
    return {"memories": memories, "count": len(memories)}


@router.get("/long")
def list_long_term_memories(limit: int = 20):
    from agentnexus.memory.long_term import get_long_term_memory

    ltm = get_long_term_memory()
    memories = ltm.list_recent(limit)
    return {"memories": memories, "count": len(memories)}


@router.post("/search")
def search_memories(req: SearchMemoryRequest):
    from agentnexus.memory.long_term import get_long_term_memory

    ltm = get_long_term_memory()
    try:
        results = ltm.search(query_text=req.query, limit=req.limit)
        return {"results": results, "query": req.query}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/clear")
def clear_memories():
    from agentnexus.memory.long_term import get_long_term_memory

    ltm = get_long_term_memory()
    ltm.clear_all()
    return {"status": "cleared"}


@router.delete("/{memory_id}")
def delete_memory(memory_id: str):
    from agentnexus.memory.long_term import get_long_term_memory

    ltm = get_long_term_memory()
    try:
        ltm.delete(memory_id)
        return {"status": "deleted", "memory_id": memory_id}
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e))


def _strip_workflow_context(content: str) -> str:
    """Remove legacy workflow context prefix from user messages.

    Old sessions stored enhanced_question which prepended workflow context
    to the user's actual question. This strips that prefix so the frontend
    displays only the user's real question.
    """
    marker = "== User Question =="
    idx = content.find(marker)
    if idx >= 0:
        return content[idx + len(marker):].lstrip("\n")
    return content


@router.get("/short")
def list_short_term_memories():
    from agentnexus.server.app import _get_runtime

    runtime = _get_runtime()
    stm = runtime.memory_manager.short_term
    messages = stm.get_all()
    result = []
    for m in messages:
        role = m.get("role", "")
        content = m.get("content", "")
        # Strip workflow context prefix from user messages (legacy data)
        if role == "user":
            content = _strip_workflow_context(content)
        result.append({"role": role, "content": content, "ts": m.get("ts")})
    return {"messages": result, "count": len(result)}


@router.post("/short/clear")
def clear_short_term_memory():
    """Clear the global short-term memory — called when creating a new session."""
    from agentnexus.server.app import _get_runtime

    runtime = _get_runtime()
    stm = runtime.memory_manager.short_term
    stm.clear()
    return {"status": "cleared"}


@router.get("/short/history")
def list_session_history(limit: int = 0, session_id: str | None = None):
    """Read full conversation history from the message journal.

    Unlike /short (which reads from the in-memory STM deque, max 50 messages),
    this reads from the durable conversation_messages table — no message loss
    after compaction.

    Args:
        limit: Max messages to return (0 = all).
        session_id: Optional session ID to read from. If not provided,
                    uses the first active session or finds the latest.
    """
    from agentnexus.core.config import get_settings
    from agentnexus.memory.versioned import ConversationVersionManager
    from agentnexus.server.app import _get_runtime

    runtime = _get_runtime()
    settings = get_settings()
    workspace = str(__import__("pathlib").Path.cwd())

    # Use provided session_id, or fall back to first active session, or find latest
    if not session_id:
        chat = runtime.services.chat
        if chat._sessions:
            session_id = next(iter(chat._sessions.keys()))

    if not session_id:
        session_id = ConversationVersionManager.find_latest_session(
            settings.memory_db_path, workspace
        )
    if not session_id:
        return {"messages": [], "count": 0}

    version = ConversationVersionManager(
        session_id, settings.memory_db_path,
        workspace_path=workspace,
    )
    messages = version.get_messages(limit=limit)
    result = []
    for m in messages:
        role = m.get("role", "")
        content = m.get("content", "")
        if role == "user":
            content = _strip_workflow_context(content)
        result.append({"role": role, "content": content, "ts": m.get("ts")})
    return {"messages": result, "count": len(result), "session_id": session_id}


@router.post("/reflect")
def run_reflection(days: int = 7, max_memories: int = 50):
    """Trigger periodic reflection: distill higher-level patterns from recent memories.

    Reviews note-category memories from the last N days, identifies recurring patterns,
    and saves distilled insights as fact/preference memories.
    """
    from agentnexus.core.llm import AgentLLM
    from agentnexus.memory.long_term import get_long_term_memory
    from agentnexus.memory.reflection import run_reflection as _run_reflection
    from agentnexus.rag.embeddings import get_embedding_model

    ltm = get_long_term_memory()
    if not ltm:
        raise HTTPException(status_code=503, detail="Long-term memory not available")

    llm = AgentLLM()
    embed_model = get_embedding_model()

    result = _run_reflection(
        llm=llm,
        embed_model=embed_model,
        long_term=ltm,
        session_id="reflection",
        days=days,
        max_memories=max_memories,
    )
    return result
