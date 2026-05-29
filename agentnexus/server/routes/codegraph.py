"""Code graph API routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

router = APIRouter(tags=["codegraph"])


@router.get("/search")
def search_entities(query: str, kind: str | None = None, limit: int = 10):
    from agentnexus.codegraph.queries import search_entities as _search

    results = _search(query, kind=kind, limit=limit)
    return {"results": [r.__dict__ if hasattr(r, "__dict__") else r for r in results]}


@router.get("/callers")
def get_callers(symbol: str, depth: int = 1):
    from agentnexus.codegraph.queries import get_callers as _callers

    results = _callers(symbol, depth=depth)
    return {"symbol": symbol, "callers": [r.__dict__ if hasattr(r, "__dict__") else r for r in results]}


@router.get("/callees")
def get_callees(symbol: str, depth: int = 1):
    from agentnexus.codegraph.queries import get_callees as _callees

    results = _callees(symbol, depth=depth)
    return {"symbol": symbol, "callees": [r.__dict__ if hasattr(r, "__dict__") else r for r in results]}


@router.get("/inherits")
def get_inherits(cls: str):
    from agentnexus.codegraph.queries import get_inheritance_tree as _inherits

    results = _inherits(cls)
    return {"class": cls, "tree": results if isinstance(results, (dict, list)) else str(results)}


@router.get("/imports")
def get_imports(module: str):
    from agentnexus.codegraph.queries import get_imports as _imports

    results = _imports(module)
    return {"module": module, "imports": results if isinstance(results, (dict, list)) else str(results)}


@router.get("/context")
def get_context(symbol: str):
    from agentnexus.codegraph.queries import get_entity_context as _context

    result = _context(symbol)
    return result if isinstance(result, dict) else {"symbol": symbol, "context": str(result)}


@router.get("/stats")
def codegraph_stats():
    from agentnexus.codegraph.store import CodeGraphStore
    from agentnexus.core.config import get_settings

    settings = get_settings()
    store = CodeGraphStore(settings.codegraph_db_path)
    return store.get_stats()


@router.post("/build")
def build_graph(force: bool = False):
    from agentnexus.codegraph.updater import build_graph as _build

    try:
        result = _build(force=force)
        return {"status": "ok", "result": result if isinstance(result, dict) else str(result)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
