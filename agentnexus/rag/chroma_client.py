"""Deprecated compatibility shim — use ``agentnexus.storage.chroma`` instead.

All public names are re-exported from :mod:`agentnexus.storage.chroma` and
:mod:`agentnexus.rag.embeddings`.  Importing from this module will emit a
:class:`DeprecationWarning` in a future release.
"""

from agentnexus.rag.embeddings import (  # noqa: F401
    _EMBED_BATCH_SIZE,
    _EMBED_TORCH_THREADS_CAP,
    VECTOR_DIM,
    _configure_embedding_runtime,
    _fallback_tokenize,
    _FallbackEmbeddingModel,
    _resolve_embedding_device,
    get_embedding_model,
    reset_embedding_model,
)
from agentnexus.rag.embeddings import (
    embed_texts as _embed_texts,
)
from agentnexus.rag.embeddings import (
    embedding_to_list as _embedding_to_list,
)
from agentnexus.storage.chroma import (  # noqa: F401
    COLLECTION_NAME,
    DEFAULT_COLLECTION_METADATA,
    chroma_operation_lock,
    chunk_metadata_to_chroma,
    delete_collection,
    delete_documents,
    get_chroma_client,
    get_collection,
    insert_documents,
    normalize_metadata_for_chroma,
    resolve_collection_name,
    search,
    upsert_documents,
)
from agentnexus.storage.chroma import (
    ThreadSafeChromaCollection as _ThreadSafeChromaCollection,
)
from agentnexus.storage.chroma import (
    normalize_chroma_metadata_value as _normalize_chroma_metadata_value,
)
from agentnexus.storage.chroma import (
    reset_storage_client as _reset_chroma_client,
)

__all__ = [
    "COLLECTION_NAME",
    "DEFAULT_COLLECTION_METADATA",
    "VECTOR_DIM",
    "_EMBED_BATCH_SIZE",
    "_EMBED_TORCH_THREADS_CAP",
    "_FallbackEmbeddingModel",
    "_ThreadSafeChromaCollection",
    "_configure_embedding_runtime",
    "_embed_texts",
    "_embedding_to_list",
    "_fallback_tokenize",
    "_normalize_chroma_metadata_value",
    "_reset_chroma_client",
    "_resolve_embedding_device",
    "chroma_operation_lock",
    "chunk_metadata_to_chroma",
    "delete_collection",
    "delete_documents",
    "get_chroma_client",
    "get_collection",
    "get_embedding_model",
    "insert_documents",
    "normalize_metadata_for_chroma",
    "reset_embedding_model",
    "resolve_collection_name",
    "search",
    "upsert_documents",
]
