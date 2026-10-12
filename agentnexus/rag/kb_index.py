"""Keep KB vectors in the same embedding space as the configured model."""

from __future__ import annotations

import hashlib
import logging
import threading
from collections.abc import Callable
from pathlib import Path

from filelock import FileLock

from agentnexus.core.config import get_settings
from agentnexus.rag.models import KnowledgeBaseRecord
from agentnexus.rag.store import get_knowledge_base_catalog
from agentnexus.storage.chroma import (
    base_collection_name,
    chunk_metadata_to_chroma,
    delete_collection,
    get_collection,
    upsert_documents,
)

logger = logging.getLogger(__name__)
_locks: dict[tuple[str, str], FileLock] = {}
_locks_guard = threading.Lock()


def index_lock(namespace: str) -> FileLock:
    db_path = get_settings().rag_catalog_db_path
    key = (db_path, namespace)
    with _locks_guard:
        if key not in _locks:
            digest = hashlib.sha256(namespace.encode("utf-8")).hexdigest()[:16]
            path = Path(f"{db_path}.{digest}.index.lock")
            path.parent.mkdir(parents=True, exist_ok=True)
            _locks[key] = FileLock(str(path))
        return _locks[key]


def _generation_name(namespace: str, identity: str) -> str:
    base = base_collection_name(namespace)
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
    return f"{base[:48]}_model_{digest}"


def active_index_identity(namespace: str) -> str | None:
    kb = get_knowledge_base_catalog().get_knowledge_base(namespace)
    if kb is None:
        return None
    value = kb.metadata.get("embedding_identity")
    return value if isinstance(value, str) else None


def ensure_embedding_index(
    namespace: str,
    model,
    identity: str,
    *,
    progress: Callable[[str], None] | None = None,
) -> str:
    """Build an isolated generation, then switch the catalog pointer.

    An unmarked legacy index may contain hash vectors. We never reuse it, even
    when its dimensions match the real embedding model. A failed rebuild leaves
    the old collection and catalog documents intact; the next call retries.
    """
    with index_lock(namespace):
        catalog = get_knowledge_base_catalog()
        kb = catalog.get_knowledge_base(namespace)
        if kb is None:
            base = base_collection_name(namespace)
            kb = KnowledgeBaseRecord(
                kb_id=base, namespace=namespace, display_name=namespace, collection_name=base,
            )
            catalog.upsert_knowledge_base(kb)
        if kb.metadata.get("embedding_identity") == identity:
            count = get_collection(name=kb.collection_name).count()
            if count == catalog.count_chunks_by_kb(kb.kb_id):
                return kb.collection_name

        chunks = catalog.list_chunks_by_kb(kb.kb_id)
        target = _generation_name(namespace, identity)
        if target == kb.collection_name:
            target += "_rebuild"
        if progress is not None:
            progress(f"Rebuilding {len(chunks)} knowledge chunks with the current embedding model")
        logger.info("Rebuilding KB %s into %s using %s (%d chunks)", namespace, target, identity, len(chunks))
        delete_collection(name=target)
        if chunks:
            upsert_documents(
                [chunk.indexed_text or chunk.text for chunk in chunks],
                metadatas=[chunk_metadata_to_chroma(chunk) for chunk in chunks],
                ids=[chunk.chunk_id for chunk in chunks],
                name=target,
                embed_texts_provider=lambda texts: _encode(model, texts),
            )
        indexed_ids = set(get_collection(name=target).get(include=[])["ids"])
        expected_ids = {chunk.chunk_id for chunk in chunks}
        if indexed_ids != expected_ids:
            raise RuntimeError(
                f"知识库重建未完成：预期 {len(expected_ids)} 个向量，实际 {len(indexed_ids)} 个"
            )
        retired = set(kb.metadata.get("retired_collections", []))
        if chunks:
            retired.add(kb.collection_name)
        retired.discard(target)
        kb.collection_name = target
        kb.metadata = {
            **kb.metadata,
            "embedding_identity": identity,
            "retired_collections": sorted(retired),
        }
        catalog.upsert_knowledge_base(kb)
        return target


def _encode(model, texts: list[str]) -> list[list[float]]:
    values = model.encode(texts, normalize_embeddings=True, batch_size=1024, show_progress_bar=False)
    return values.tolist() if hasattr(values, "tolist") else values
