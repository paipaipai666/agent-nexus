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
