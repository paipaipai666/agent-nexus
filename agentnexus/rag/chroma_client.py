from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from agentnexus.core.config import get_settings
from agentnexus.rag import embeddings as embedding_service
from agentnexus.storage import chroma as storage_chroma

from .models import ChunkRecord

COLLECTION_NAME = "documents"
VECTOR_DIM = embedding_service.VECTOR_DIM
DEFAULT_COLLECTION_METADATA = storage_chroma.DEFAULT_COLLECTION_METADATA

_client = None
_client_path: str | None = None
_collections: dict[str, Any] = {}
_model = None
_model_name: str | None = None
_model_device: str | None = None
_EMBED_BATCH_SIZE = embedding_service._EMBED_BATCH_SIZE
_EMBED_TORCH_THREADS_CAP = embedding_service._EMBED_TORCH_THREADS_CAP
_chroma_lock = storage_chroma._chroma_lock


@contextmanager
def chroma_operation_lock() -> Iterator[None]:
    """Serialize ChromaDB Rust binding calls within this process.

    ChromaDB 1.5.8 can fail under concurrent PersistentClient/collection
    access from multiple threads. Keep this lock at the boundary where code
    enters ChromaDB so RAG and LTM share the same synchronization primitive.
    """
    with storage_chroma.chroma_operation_lock():
        yield


class _ThreadSafeChromaCollection:
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


_FallbackEmbeddingModel = embedding_service._FallbackEmbeddingModel


def _fallback_tokenize(text: str) -> list[str]:
    return embedding_service._fallback_tokenize(text)


def resolve_collection_name(name: str | None = None, namespace: str | None = None) -> str:
    return storage_chroma.resolve_collection_name(name=name, namespace=namespace)


def get_chroma_client():
    global _client, _client_path, _collections
    client = storage_chroma.get_chroma_client()
    _client = storage_chroma._client
    _client_path = storage_chroma._client_path
    _collections = storage_chroma._collections
    return client


def _resolve_embedding_device() -> str:
    return embedding_service.resolve_embedding_device()


def _configure_embedding_runtime(device: str) -> None:
    embedding_service.configure_embedding_runtime(device)


def get_embedding_model():
    global _model, _model_name, _model_device
    embedding_service._model = _model
    embedding_service._model_name = _model_name
    embedding_service._model_device = _model_device
    embedding_service._FallbackEmbeddingModel = _FallbackEmbeddingModel
    embedding_service.get_embedding_model(
        settings_provider=get_settings,
        device_resolver=_resolve_embedding_device,
        runtime_configurer=_configure_embedding_runtime,
    )
    _model = embedding_service._model
    _model_name = embedding_service._model_name
    _model_device = embedding_service._model_device
    return _model


def _embedding_to_list(value):
    return embedding_service.embedding_to_list(value)


def get_collection(
    name: str | None = None,
    namespace: str | None = None,
    metadata: dict | None = None,
):
    collection = storage_chroma.get_collection(name=name, namespace=namespace, metadata=metadata)
    global _client, _client_path, _collections
    _client = storage_chroma._client
    _client_path = storage_chroma._client_path
    _collections = storage_chroma._collections
    return collection


def _validate_payload_lengths(
    texts: list[str],
    metadatas: list[dict] | None = None,
    ids: list[str] | None = None,
):
    storage_chroma._validate_payload_lengths(texts, metadatas=metadatas, ids=ids)


def _build_ids(texts: list[str], ids: list[str] | None = None) -> list[str]:
    return storage_chroma._build_ids(texts, ids=ids)


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
    return _embedding_to_list(embeddings)


def _normalize_chroma_metadata_value(value: Any) -> str | int | float | bool:
    return storage_chroma.normalize_chroma_metadata_value(value)


def normalize_metadata_for_chroma(metadata: dict[str, Any] | None) -> dict[str, str | int | float | bool]:
    return storage_chroma.normalize_metadata_for_chroma(metadata)



def chunk_metadata_to_chroma(chunk: ChunkRecord) -> dict[str, str | int | float | bool]:
    return storage_chroma.chunk_metadata_to_chroma(chunk)


def insert_documents(
    texts: list[str],
    metadatas: list[dict] | None = None,
    ids: list[str] | None = None,
    name: str | None = None,
    namespace: str | None = None,
    metadata: dict | None = None,
) -> list[str]:
    return storage_chroma.insert_documents(
        texts,
        metadatas=metadatas,
        ids=ids,
        name=name,
        namespace=namespace,
        metadata=metadata,
        collection_provider=get_collection,
        embed_texts_provider=_embed_texts,
    )


def upsert_documents(
    texts: list[str],
    metadatas: list[dict] | None = None,
    ids: list[str] | None = None,
    name: str | None = None,
    namespace: str | None = None,
    metadata: dict | None = None,
) -> list[str]:
    return storage_chroma.upsert_documents(
        texts,
        metadatas=metadatas,
        ids=ids,
        name=name,
        namespace=namespace,
        metadata=metadata,
        collection_provider=get_collection,
        embed_texts_provider=_embed_texts,
    )


def search(
    query: str,
    limit: int = 5,
    name: str | None = None,
    namespace: str | None = None,
    where: dict[str, Any] | None = None,
) -> list[dict]:
    return storage_chroma.search(
        query,
        limit=limit,
        name=name,
        namespace=namespace,
        where=where,
        collection_provider=get_collection,
        embedding_model_provider=get_embedding_model,
    )


def delete_collection(name: str | None = None, namespace: str | None = None):
    storage_chroma.delete_collection(name=name, namespace=namespace)


def delete_documents(
    ids: list[str] | None = None,
    where: dict[str, Any] | None = None,
    name: str | None = None,
    namespace: str | None = None,
):
    storage_chroma.delete_documents(
        ids=ids,
        where=where,
        name=name,
        namespace=namespace,
        collection_provider=get_collection,
    )


def _reset_chroma_client(reset_model: bool = False):
    global _client, _client_path, _collections, _model, _model_name, _model_device
    storage_chroma.reset_storage_client()
    _client = None
    _client_path = None
    _collections = {}
    if reset_model:
        _model = None
        _model_name = None
        _model_device = None
        embedding_service.reset_embedding_model()
