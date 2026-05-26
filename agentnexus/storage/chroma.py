"""Shared ChromaDB storage primitives.

This module owns Chroma client, collection, locking, and metadata normalization
for both RAG and long-term memory. ``agentnexus.rag.chroma_client`` remains as a
compatibility layer for older imports and tests.
"""

from __future__ import annotations

import json
import re
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from agentnexus.core.config import get_settings
from agentnexus.rag import embeddings as embedding_service

COLLECTION_NAME = "documents"
DEFAULT_COLLECTION_METADATA = {"hnsw:space": "cosine"}

_client = None
_client_path: str | None = None
_collections: dict[str, Any] = {}
_chroma_lock = threading.RLock()


@contextmanager
def chroma_operation_lock() -> Iterator[None]:
    """Serialize ChromaDB Rust binding calls within this process."""
    with _chroma_lock:
        yield


class ThreadSafeChromaCollection:
    def __init__(self, collection):
        self._collection = collection

    def __getattr__(self, name: str):
        attr = getattr(self._collection, name)
        if not callable(attr):
            return attr

        def _locked_call(*args, **kwargs):
            with chroma_operation_lock():
                return attr(*args, **kwargs)

        return _locked_call


def resolve_collection_name(name: str | None = None, namespace: str | None = None) -> str:
    if name:
        return name
    if namespace:
        settings = get_settings()
        normalized = re.sub(r"[^0-9A-Za-z_.-]+", "_", namespace.strip()).strip("_.-")
        normalized = normalized or settings.rag_default_namespace
        return f"{settings.rag_collection_prefix}{normalized}"
    return COLLECTION_NAME


def get_chroma_client():
    global _client, _client_path, _collections
    settings = get_settings()
    with chroma_operation_lock():
        if _client is None or _client_path != settings.chroma_persist_dir:
            import chromadb

            _client = chromadb.PersistentClient(path=settings.chroma_persist_dir)
            _client_path = settings.chroma_persist_dir
            _collections = {}
        return _client


def get_collection(
    name: str | None = None,
    namespace: str | None = None,
    metadata: dict | None = None,
):
    collection_name = resolve_collection_name(name=name, namespace=namespace)
    with chroma_operation_lock():
        if collection_name not in _collections:
            client = get_chroma_client()
            collection = client.get_or_create_collection(
                name=collection_name,
                metadata=metadata or DEFAULT_COLLECTION_METADATA,
            )
            _collections[collection_name] = ThreadSafeChromaCollection(collection)
        return _collections[collection_name]


def normalize_chroma_metadata_value(value: Any) -> str | int | float | bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (str, int, float)):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def normalize_metadata_for_chroma(metadata: dict[str, Any] | None) -> dict[str, str | int | float | bool]:
    if not metadata:
        return {}

    normalized: dict[str, str | int | float | bool] = {}
    for key, value in metadata.items():
        if key == "heading_path":
            path_parts = [part for part in value if isinstance(part, str) and part] if isinstance(value, list) else []
            if path_parts:
                normalized["heading_path_text"] = " / ".join(path_parts)
                normalized["heading_depth"] = len(path_parts)
            continue
        if value is None:
            continue
        normalized[key] = normalize_chroma_metadata_value(value)
    return normalized


def chunk_metadata_to_chroma(chunk) -> dict[str, str | int | float | bool]:
    metadata = normalize_metadata_for_chroma(chunk.metadata)
    metadata.setdefault("chunk_id", chunk.chunk_id)
    metadata.setdefault("document_id", chunk.document_id)
    metadata.setdefault("document_version", chunk.document_version)
    metadata.setdefault("chunk_index", chunk.chunk_index)
    if chunk.section_index is not None:
        metadata["section_index"] = chunk.section_index
    if chunk.page_number is not None:
        metadata["page_number"] = chunk.page_number
    return metadata


def delete_collection(name: str | None = None, namespace: str | None = None):
    collection_name = resolve_collection_name(name=name, namespace=namespace)
    client = get_chroma_client()
    with chroma_operation_lock():
        try:
            client.delete_collection(collection_name)
        except Exception as exc:
            from rich.console import Console

            Console().print(f"[yellow]ChromaDB 删除集合异常: {exc}[/yellow]")
        _collections.pop(collection_name, None)


def reset_storage_client() -> None:
    global _client, _client_path, _collections
    with chroma_operation_lock():
        _client = None
        _client_path = None
        _collections = {}


def _validate_payload_lengths(
    texts: list[str],
    metadatas: list[dict] | None = None,
    ids: list[str] | None = None,
):
    if metadatas is not None and len(metadatas) != len(texts):
        raise ValueError("metadatas length must match texts length")
    if ids is not None and len(ids) != len(texts):
        raise ValueError("ids length must match texts length")


def _build_ids(texts: list[str], ids: list[str] | None = None) -> list[str]:
    if ids is not None:
        return ids
    return [uuid.uuid4().hex for _ in range(len(texts))]


def _embed_texts(texts: list[str]) -> list[list[float]]:
    return embedding_service.embed_texts(texts)


def insert_documents(
    texts: list[str],
    metadatas: list[dict] | None = None,
    ids: list[str] | None = None,
    name: str | None = None,
    namespace: str | None = None,
    metadata: dict | None = None,
    *,
    collection_provider=get_collection,
    embed_texts_provider=_embed_texts,
) -> list[str]:
    if not texts:
        return []
    _validate_payload_lengths(texts, metadatas=metadatas, ids=ids)
    resolved_ids = _build_ids(texts, ids=ids)
    resolved_metadatas = (
        [normalize_metadata_for_chroma(item) for item in metadatas]
        if metadatas is not None
        else None
    )
    collection = collection_provider(name=name, namespace=namespace, metadata=metadata)
    collection.add(
        ids=resolved_ids,
        embeddings=embed_texts_provider(texts),
        documents=texts,
        metadatas=resolved_metadatas,
    )
    return resolved_ids


def upsert_documents(
    texts: list[str],
    metadatas: list[dict] | None = None,
    ids: list[str] | None = None,
    name: str | None = None,
    namespace: str | None = None,
    metadata: dict | None = None,
    *,
    collection_provider=get_collection,
    embed_texts_provider=_embed_texts,
) -> list[str]:
    if not texts:
        return []
    _validate_payload_lengths(texts, metadatas=metadatas, ids=ids)
    resolved_ids = _build_ids(texts, ids=ids)
    resolved_metadatas = (
        [normalize_metadata_for_chroma(item) for item in metadatas]
        if metadatas is not None
        else None
    )
    collection = collection_provider(name=name, namespace=namespace, metadata=metadata)
    collection.upsert(
        ids=resolved_ids,
        embeddings=embed_texts_provider(texts),
        documents=texts,
        metadatas=resolved_metadatas,
    )
    return resolved_ids


def search(
    query: str,
    limit: int = 5,
    name: str | None = None,
    namespace: str | None = None,
    where: dict[str, Any] | None = None,
    *,
    collection_provider=get_collection,
    embedding_model_provider=embedding_service.get_embedding_model,
) -> list[dict]:
    collection = collection_provider(name=name, namespace=namespace)
    model = embedding_model_provider()
    query_vec = embedding_service.embedding_to_list(model.encode(query, normalize_embeddings=True))
    results = collection.query(
        query_embeddings=[query_vec],
        n_results=limit,
        include=["documents", "distances", "metadatas"],
        where=where,
    )
    if not results["ids"] or not results["ids"][0]:
        return []

    metadatas = results.get("metadatas") or []
    metadata_rows = metadatas[0] if metadatas else []
    payload = []
    for index, rid in enumerate(results["ids"][0]):
        payload.append(
            {
                "id": rid,
                "score": 1.0 - results["distances"][0][index],
                "text": results["documents"][0][index],
                "metadata": metadata_rows[index] if index < len(metadata_rows) and metadata_rows[index] else {},
            }
        )
    return payload


def delete_documents(
    ids: list[str] | None = None,
    where: dict[str, Any] | None = None,
    name: str | None = None,
    namespace: str | None = None,
    *,
    collection_provider=get_collection,
):
    if not ids and not where:
        return
    collection = collection_provider(name=name, namespace=namespace)
    collection.delete(ids=ids, where=where)


__all__ = [
    "COLLECTION_NAME",
    "DEFAULT_COLLECTION_METADATA",
    "ThreadSafeChromaCollection",
    "chroma_operation_lock",
    "chunk_metadata_to_chroma",
    "delete_collection",
    "delete_documents",
    "get_chroma_client",
    "get_collection",
    "insert_documents",
    "normalize_chroma_metadata_value",
    "normalize_metadata_for_chroma",
    "reset_storage_client",
    "resolve_collection_name",
    "search",
    "upsert_documents",
]
