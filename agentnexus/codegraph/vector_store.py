"""ChromaDB vector store for code entity embeddings.

Manages the 'codegraph' collection for semantic search over code entities.
Uses Blue-Green update strategy: new embeddings written before old ones deleted.
"""

from __future__ import annotations

import logging
from typing import Any

from agentnexus.codegraph.models import NodeData
from agentnexus.storage.chroma import (
    chroma_operation_lock,
    get_collection,
)

logger = logging.getLogger(__name__)

# Collection name for codegraph embeddings
COLLECTION_NAME = "codegraph"
COLLECTION_METADATA = {"hnsw:space": "cosine"}


def _get_codegraph_collection():
    """Get or create the codegraph ChromaDB collection."""
    return get_collection(
        name=COLLECTION_NAME,
        metadata=COLLECTION_METADATA,
    )


def upsert_nodes(
    nodes: list[NodeData],
    embeddings: list[list[float] | None],
    content_hash: str,
) -> int:
    """Write embeddings for code entities to ChromaDB.

    Uses Blue-Green strategy: writes new embeddings, old ones remain
    until explicitly deleted by content_hash comparison.

    Returns the number of embeddings written.
    """
    if not nodes:
        return 0

    # Filter to nodes that have embeddings
    ids: list[str] = []
    texts: list[str] = []
    vecs: list[list[float]] = []
    metas: list[dict[str, str | int | float | bool]] = []

    for node, vec in zip(nodes, embeddings):
        if vec is None:
            continue
        ids.append(node.id)
        texts.append(node.qualified_name)  # document text is the qualified name
        vecs.append(vec)
        metas.append({
            "file_path": node.file_path,
            "content_hash": content_hash,
            "node_kind": node.kind,
            "node_name": node.name,
            "qualified_name": node.qualified_name,
            "language": node.language,
        })

    if not ids:
        return 0

    collection = _get_codegraph_collection()
    with chroma_operation_lock():
        collection.upsert(
            ids=ids,
            embeddings=vecs,
            documents=texts,
            metadatas=metas,
        )
    return len(ids)


def delete_by_file(file_path: str, exclude_content_hash: str | None = None) -> int:
    """Delete embeddings for a file.

    If exclude_content_hash is provided, only delete embeddings with a different
    content hash (Blue-Green cleanup).
    """
    collection = _get_codegraph_collection()
    with chroma_operation_lock():
        if exclude_content_hash:
            where = {
                "$and": [
                    {"file_path": {"$eq": file_path}},
                    {"content_hash": {"$ne": exclude_content_hash}},
                ]
            }
        else:
            where = {"file_path": {"$eq": file_path}}

        # Get IDs to delete for count
        try:
            results = collection.get(where=where, include=[])
            if not results["ids"]:
                return 0
            collection.delete(ids=results["ids"])
            return len(results["ids"])
        except Exception as e:
            logger.warning(f"ChromaDB delete failed: {e}")
            return 0


def delete_by_ids(ids: list[str]) -> int:
    """Delete embeddings by their IDs."""
    if not ids:
        return 0
    collection = _get_codegraph_collection()
    with chroma_operation_lock():
        collection.delete(ids=ids)
    return len(ids)


def search_semantic(
    query_embedding: list[float],
    limit: int = 20,
    kind: str | None = None,
    language: str | None = None,
) -> list[dict[str, Any]]:
    """Semantic search over code entity embeddings.

    Returns list of {id, score, metadata} dicts sorted by relevance.
    """
    collection = _get_codegraph_collection()

    # Build where filter
    where: dict | None = None
    filters: list[dict] = []
    if kind:
        filters.append({"node_kind": {"$eq": kind}})
    if language:
        filters.append({"language": {"$eq": language}})
    if len(filters) == 1:
        where = filters[0]
    elif len(filters) > 1:
        where = {"$and": filters}

    with chroma_operation_lock():
        results = collection.query(
            query_embeddings=[query_embedding],
            n_results=limit,
            include=["documents", "distances", "metadatas"],
            where=where,
        )

    if not results["ids"] or not results["ids"][0]:
        return []

    metadatas = results.get("metadatas") or []
    meta_rows = metadatas[0] if metadatas else []
    payload: list[dict[str, Any]] = []
    for i, rid in enumerate(results["ids"][0]):
        payload.append({
            "id": rid,
            "score": 1.0 - results["distances"][0][i],
            "document": results["documents"][0][i],
            "metadata": meta_rows[i] if i < len(meta_rows) and meta_rows[i] else {},
        })
    return payload


def get_all_ids() -> set[str]:
    """Get all document IDs in the codegraph collection."""
    collection = _get_codegraph_collection()
    with chroma_operation_lock():
        try:
            results = collection.get(include=[])
            return set(results["ids"])
        except Exception:
            return set()


def clear_collection() -> None:
    """Delete all embeddings in the codegraph collection.

    Used by --force full rebuild.
    """
    from agentnexus.storage.chroma import delete_collection
    delete_collection(name=COLLECTION_NAME)


__all__ = [
    "COLLECTION_NAME",
    "upsert_nodes",
    "delete_by_file",
    "delete_by_ids",
    "search_semantic",
    "get_all_ids",
    "clear_collection",
]
