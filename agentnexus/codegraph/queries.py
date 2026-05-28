"""Code knowledge graph queries.

Provides semantic search, graph queries, and hybrid search
that combines both for optimal code navigation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agentnexus.codegraph import embeddings as codegraph_embeddings
from agentnexus.codegraph import vector_store
from agentnexus.codegraph.store import CodeGraphStore, detect_project_root, get_db_path

logger = logging.getLogger(__name__)

# Scoring weights
_SEMANTIC_WEIGHT = 0.8  # alpha for hybrid scoring


@dataclass
class SearchResult:
    """A single search result."""

    id: str
    kind: str
    name: str
    qualified_name: str
    file_path: str
    start_line: int
    end_line: int
    score: float
    docstring: str | None = None
    signature: str | None = None
    visibility: str | None = None
    is_async: bool = False
    decorators: list[str] | None = None
    metadata: dict | None = None


def _get_store(project_root: Path | None = None) -> tuple[CodeGraphStore, Path]:
    """Get a store instance and the project root."""
    if project_root is None:
        project_root = detect_project_root()
    db_path = get_db_path(project_root)
    store = CodeGraphStore(db_path)
    store.init_schema()
    return store, project_root


def _node_to_result(node: dict, score: float = 1.0) -> SearchResult:
    """Convert a node dict to a SearchResult."""
    return SearchResult(
        id=node["id"],
        kind=node["kind"],
        name=node["name"],
        qualified_name=node["qualified_name"],
        file_path=node["file_path"],
        start_line=node["start_line"],
        end_line=node["end_line"],
        score=score,
        docstring=node.get("docstring"),
        signature=node.get("signature"),
        visibility=node.get("visibility"),
        is_async=bool(node.get("is_async")),
        decorators=node.get("decorators"),
    )


def search_entities(
    query: str,
    kind: str | None = None,
    limit: int = 10,
    project_root: Path | None = None,
) -> list[SearchResult]:
    """Semantic search for code entities.

    Args:
        query: Natural language search query.
        kind: Optional filter by NodeKind.
        limit: Maximum results to return.
        project_root: Project root directory.

    Returns:
        List of SearchResult sorted by relevance.
    """
    store, _ = _get_store(project_root)
    try:
        # Generate query embedding
        model = codegraph_embeddings.get_embedding_model()
        from agentnexus.rag.embeddings import embedding_to_list
        query_vec = embedding_to_list(model.encode(query, normalize_embeddings=True))

        # Search ChromaDB
        chroma_results = vector_store.search_semantic(
            query_embedding=query_vec,
            limit=limit * 2,  # fetch extra for dedup
            kind=kind,
        )

        # Build results
        results: list[SearchResult] = []
        seen_ids: set[str] = set()

        for r in chroma_results:
            node_id = r["id"]
            if node_id in seen_ids:
                continue
            seen_ids.add(node_id)

            node = store.get_node(node_id)
            if node:
                results.append(_node_to_result(node, score=r["score"]))
            else:
                # Node in ChromaDB but not SQLite - use metadata
                meta = r.get("metadata", {})
                results.append(SearchResult(
                    id=node_id,
                    kind=meta.get("node_kind", "unknown"),
                    name=meta.get("node_name", ""),
                    qualified_name=meta.get("qualified_name", ""),
                    file_path=meta.get("file_path", ""),
                    start_line=0,
                    end_line=0,
                    score=r["score"],
                ))

        return results[:limit]
    finally:
        store.close()


def get_callers(
    symbol: str,
    depth: int = 2,
    project_root: Path | None = None,
) -> list[SearchResult]:
    """Find nodes that call the given symbol."""
    store, _ = _get_store(project_root)
    try:
        node_id = _resolve_symbol(store, symbol)
        if not node_id:
            return []

        callers_data = store.get_callers(node_id, depth=depth)
        return [_node_to_result(n) for n in callers_data]
    finally:
        store.close()


def get_callees(
    symbol: str,
    depth: int = 2,
    project_root: Path | None = None,
) -> list[SearchResult]:
    """Find nodes called by the given symbol."""
    store, _ = _get_store(project_root)
    try:
        node_id = _resolve_symbol(store, symbol)
        if not node_id:
            return []

        callees_data = store.get_callees(node_id, depth=depth)
        return [_node_to_result(n) for n in callees_data]
    finally:
        store.close()


def get_inheritance_tree(
    class_name: str,
    project_root: Path | None = None,
) -> list[SearchResult]:
    """Get the inheritance hierarchy for a class."""
    store, _ = _get_store(project_root)
    try:
        node_id = _resolve_symbol(store, class_name, kind="class")
        if not node_id:
            return []

        tree = store.get_inheritance_tree(node_id)
        return [_node_to_result(n) for n in tree]
    finally:
        store.close()


def get_imports(
    module_path: str,
    project_root: Path | None = None,
) -> list[SearchResult]:
    """Get import relationships for a module."""
    store, _ = _get_store(project_root)
    try:
        imports_data = store.get_imports(module_path)
        return [_node_to_result(n) for n in imports_data]
    finally:
        store.close()


def get_entity_context(
    symbol: str,
    project_root: Path | None = None,
) -> dict[str, Any] | None:
    """Get full context for a code entity.

    Returns entity details plus callers, callees, and related entities.
    """
    store, _ = _get_store(project_root)
    try:
        node_id = _resolve_symbol(store, symbol)
        if not node_id:
            return None

        node = store.get_node(node_id)
        if not node:
            return None

        callers = [_node_to_result(n) for n in store.get_callers(node_id, depth=1)]
        callees = [_node_to_result(n) for n in store.get_callees(node_id, depth=1)]

        # Get containing file info
        file_info = store.get_file(node["file_path"])

        return {
            "entity": _node_to_result(node),
            "callers": callers,
            "callees": callees,
            "file": file_info,
        }
    finally:
        store.close()


def _resolve_symbol(
    store: CodeGraphStore,
    symbol: str,
    kind: str | None = None,
) -> str | None:
    """Resolve a symbol name to a node ID.

    Tries exact qualified name match first, then fuzzy name match.
    """
    # Try as-is
    for k in ([kind] if kind else ["function", "method", "class"]):
        node_id = f"{k}:{symbol}"
        if store.get_node(node_id):
            return node_id

    # Search by name
    conn = store._get_conn()
    if kind:
        rows = conn.execute(
            "SELECT id FROM nodes WHERE name = ? AND kind = ? LIMIT 1",
            (symbol, kind),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id FROM nodes WHERE name = ? OR qualified_name LIKE ? LIMIT 1",
            (symbol, f"%{symbol}"),
        ).fetchall()

    if rows:
        return rows[0]["id"]

    return None


# Agent tool functions

def codegraph_search(
    query: str,
    kind: str | None = None,
    limit: int = 10,
) -> list[dict]:
    """Search code entities. Used as an agent tool."""
    results = search_entities(query, kind=kind, limit=limit)
    return [
        {
            "name": r.name,
            "kind": r.kind,
            "qualified_name": r.qualified_name,
            "file_path": r.file_path,
            "line": r.start_line,
            "score": r.score,
            "docstring": r.docstring,
            "signature": r.signature,
        }
        for r in results
    ]


def codegraph_relations(
    symbol: str,
    relation: str,
) -> list[dict]:
    """Query entity relationships. Used as an agent tool."""
    if relation == "callers":
        results = get_callers(symbol)
    elif relation == "callees":
        results = get_callees(symbol)
    elif relation == "inherits":
        results = get_inheritance_tree(symbol)
    elif relation == "imports":
        results = get_imports(symbol)
    else:
        return [{"error": f"Unknown relation type: {relation}"}]

    return [
        {
            "name": r.name,
            "kind": r.kind,
            "qualified_name": r.qualified_name,
            "file_path": r.file_path,
            "line": r.start_line,
        }
        for r in results
    ]


def codegraph_context(symbol: str) -> dict:
    """Get full entity context. Used as an agent tool."""
    ctx = get_entity_context(symbol)
    if not ctx:
        return {"error": f"Symbol not found: {symbol}"}

    entity = ctx["entity"]
    return {
        "entity": {
            "name": entity.name,
            "kind": entity.kind,
            "qualified_name": entity.qualified_name,
            "file_path": entity.file_path,
            "line": entity.start_line,
            "docstring": entity.docstring,
            "signature": entity.signature,
        },
        "callers": [
            {"name": r.name, "kind": r.kind, "file_path": r.file_path, "line": r.start_line}
            for r in ctx["callers"]
        ],
        "callees": [
            {"name": r.name, "kind": r.kind, "file_path": r.file_path, "line": r.start_line}
            for r in ctx["callees"]
        ],
    }


__all__ = [
    "SearchResult",
    "search_entities",
    "get_callers",
    "get_callees",
    "get_inheritance_tree",
    "get_imports",
    "get_entity_context",
    "codegraph_search",
    "codegraph_relations",
    "codegraph_context",
]
