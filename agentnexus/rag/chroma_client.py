import json
import os
import re
import uuid
from typing import Any

from agentnexus.core.config import get_settings

from .models import ChunkRecord

COLLECTION_NAME = "documents"
VECTOR_DIM = 512
DEFAULT_COLLECTION_METADATA = {"hnsw:space": "cosine"}

_client = None
_client_path: str | None = None
_collections: dict[str, Any] = {}
_model = None
_model_name: str | None = None
_model_device: str | None = None
_EMBED_BATCH_SIZE = 1024
_EMBED_TORCH_THREADS_CAP = 12


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
    if _client is None or _client_path != settings.chroma_persist_dir:
        import chromadb

        _client = chromadb.PersistentClient(path=settings.chroma_persist_dir)
        _client_path = settings.chroma_persist_dir
        _collections = {}
    return _client


def _resolve_embedding_device() -> str:
    try:
        import torch
    except ImportError:
        return "cpu"

    if torch.cuda.is_available():
        return "cuda"

    mps = getattr(getattr(torch, "backends", None), "mps", None)
    if mps is not None and mps.is_available():
        return "mps"

    return "cpu"


def _configure_embedding_runtime(device: str) -> None:
    if device != "cpu":
        return
    try:
        import torch
    except ImportError:
        return

    target_threads = min(max(os.cpu_count() or 1, 1), _EMBED_TORCH_THREADS_CAP)
    if torch.get_num_threads() != target_threads:
        torch.set_num_threads(target_threads)


def get_embedding_model():
    global _model, _model_name, _model_device
    settings = get_settings()
    resolved_device = _resolve_embedding_device()
    _configure_embedding_runtime(resolved_device)
    if _model is None or _model_name != settings.embedding_model or _model_device != resolved_device:
        from sentence_transformers import SentenceTransformer

        _model = SentenceTransformer(settings.embedding_model, device=resolved_device)
        _model_name = settings.embedding_model
        _model_device = resolved_device
    return _model


def get_collection(
    name: str | None = None,
    namespace: str | None = None,
    metadata: dict | None = None,
):
    collection_name = resolve_collection_name(name=name, namespace=namespace)
    if collection_name not in _collections:
        client = get_chroma_client()
        _collections[collection_name] = client.get_or_create_collection(
            name=collection_name,
            metadata=metadata or DEFAULT_COLLECTION_METADATA,
        )
    return _collections[collection_name]


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
    if not texts:
        return []
    model = get_embedding_model()
    embeddings = model.encode(
        texts,
        normalize_embeddings=True,
        batch_size=_EMBED_BATCH_SIZE,
        show_progress_bar=False,
    )
    return embeddings.tolist() if hasattr(embeddings, "tolist") else embeddings


def _normalize_chroma_metadata_value(value: Any) -> str | int | float | bool:
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
        normalized[key] = _normalize_chroma_metadata_value(value)
    return normalized



def chunk_metadata_to_chroma(chunk: ChunkRecord) -> dict[str, str | int | float | bool]:
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


def insert_documents(
    texts: list[str],
    metadatas: list[dict] | None = None,
    ids: list[str] | None = None,
    name: str | None = None,
    namespace: str | None = None,
    metadata: dict | None = None,
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
    col = get_collection(name=name, namespace=namespace, metadata=metadata)
    col.add(
        ids=resolved_ids,
        embeddings=_embed_texts(texts),
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
    col = get_collection(name=name, namespace=namespace, metadata=metadata)
    col.upsert(
        ids=resolved_ids,
        embeddings=_embed_texts(texts),
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
) -> list[dict]:
    col = get_collection(name=name, namespace=namespace)
    model = get_embedding_model()
    query_vec = model.encode(query, normalize_embeddings=True).tolist()
    results = col.query(
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


def delete_collection(name: str | None = None, namespace: str | None = None):
    collection_name = resolve_collection_name(name=name, namespace=namespace)
    client = get_chroma_client()
    try:
        client.delete_collection(collection_name)
    except Exception as e:
        from rich.console import Console

        Console().print(f"[yellow]ChromaDB 删除集合异常: {e}[/yellow]")
    _collections.pop(collection_name, None)


def delete_documents(
    ids: list[str] | None = None,
    where: dict[str, Any] | None = None,
    name: str | None = None,
    namespace: str | None = None,
):
    if not ids and not where:
        return
    col = get_collection(name=name, namespace=namespace)
    col.delete(ids=ids, where=where)


def _reset_chroma_client(reset_model: bool = False):
    global _client, _client_path, _collections, _model, _model_name, _model_device
    _client = None
    _client_path = None
    _collections = {}
    if reset_model:
        _model = None
        _model_name = None
        _model_device = None
