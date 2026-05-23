"""Performance: SQLite catalog — KnowledgeBaseCatalog operations."""

from __future__ import annotations

import time
from datetime import datetime, timezone

from agentnexus.rag.models import ChunkRecord, KnowledgeBaseRecord, SourceDocument

UPSERT_CHUNKS_1000_P95_MAX_MS = 500
LIST_CHUNKS_10000_P95_MAX_MS = 300
KB_INIT_P95_MAX_MS = 100
LIST_DOCUMENTS_1000_P95_MAX_MS = 100
METADATA_ENCODE_1000_P95_MAX_MS = 100


def _init_kb(catalog, kb_id="perf_kb", namespace="perf"):
    """Ensure a knowledge base and source document exist for foreign key constraints."""
    from datetime import datetime, timezone
    kb = KnowledgeBaseRecord(
        kb_id=kb_id, namespace=namespace,
        display_name="Perf KB", collection_name="perf_collection",
    )
    catalog.upsert_knowledge_base(kb)
    doc = SourceDocument(
        document_id="perf_doc", kb_id=kb_id,
        source_id="perf", source_uri="perf.md",
        document_version="v1",
        content=f"Perf test document for {kb_id}.",
        created_at=datetime.now(timezone.utc).isoformat(),
        updated_at=datetime.now(timezone.utc).isoformat(),
    )
    catalog.upsert_document(doc)


def _make_chunks(count: int, kb_id: str = "perf_kb") -> list[ChunkRecord]:
    now = datetime.now(timezone.utc).isoformat()
    return [
        ChunkRecord(
            chunk_id=f"chunk_{i:06d}",
            kb_id=kb_id,
            document_id="perf_doc",
            document_version="v1",
            chunk_index=i,
            text=f"This is chunk {i} with some content for performance testing of the SQLite catalog.",
            metadata={"source": "perf", "index": i, "tags": ["test", "perf"]},
            created_at=now,
            updated_at=now,
        )
        for i in range(count)
    ]


def _ensure_kb_and_chunks(catalog, count, kb_id="perf_kb", namespace="perf"):
    _init_kb(catalog, kb_id=kb_id, namespace=namespace)
    return _make_chunks(count, kb_id=kb_id)


# ── KnowledgeBaseCatalog init ─────────────────────────────────────────


def test_catalog_init(benchmark, perf_env):
    from agentnexus.rag.store import KnowledgeBaseCatalog

    db_path = str(perf_env / "catalog.db")

    def _init():
        c = KnowledgeBaseCatalog(db_path)
        c.close()

    benchmark(_init)


def test_upsert_knowledge_base(benchmark, perf_env):
    from agentnexus.rag.store import KnowledgeBaseCatalog

    db_path = str(perf_env / "catalog.db")
    catalog = KnowledgeBaseCatalog(db_path)

    record = KnowledgeBaseRecord(
        kb_id="perf_kb",
        namespace="perf",
        display_name="Perf KB",
        collection_name="perf_collection",
    )

    benchmark(catalog.upsert_knowledge_base, record)
    catalog.close()


# ── Upsert chunks ─────────────────────────────────────────────────────


def test_upsert_chunks_10(benchmark, perf_env):
    from agentnexus.rag.store import KnowledgeBaseCatalog

    db_path = str(perf_env / "catalog.db")
    catalog = KnowledgeBaseCatalog(db_path)
    _init_kb(catalog)
    chunks = _make_chunks(10)
    benchmark(catalog.upsert_chunks, chunks)
    catalog.close()


def test_upsert_chunks_100(benchmark, perf_env):
    from agentnexus.rag.store import KnowledgeBaseCatalog

    db_path = str(perf_env / "catalog.db")
    catalog = KnowledgeBaseCatalog(db_path)
    _init_kb(catalog)
    chunks = _make_chunks(100)
    benchmark(catalog.upsert_chunks, chunks)
    catalog.close()


def test_upsert_chunks_1000(benchmark, perf_env):
    from agentnexus.rag.store import KnowledgeBaseCatalog

    db_path = str(perf_env / "catalog.db")
    catalog = KnowledgeBaseCatalog(db_path)
    _init_kb(catalog)
    chunks = _make_chunks(1000)
    benchmark(catalog.upsert_chunks, chunks)

    data = benchmark.stats.stats.sorted_data
    p95 = data[int(len(data) * 0.95)] * 1000 if len(data) >= 20 else data[-1] * 1000
    assert p95 < UPSERT_CHUNKS_1000_P95_MAX_MS, f"upsert_chunks(1000) p95={p95:.0f}ms"
    catalog.close()


# ── List chunks ───────────────────────────────────────────────────────


def test_list_chunks_by_kb_10000(perf_env):
    from agentnexus.rag.store import KnowledgeBaseCatalog

    db_path = str(perf_env / "catalog.db")
    catalog = KnowledgeBaseCatalog(db_path)
    chunks = _ensure_kb_and_chunks(catalog, 10000)
    catalog.upsert_chunks(chunks)

    start = time.perf_counter()
    result = catalog.list_chunks_by_kb("perf_kb")
    elapsed = time.perf_counter() - start
    p95 = elapsed * 1000 * 1.05
    assert p95 < LIST_CHUNKS_10000_P95_MAX_MS, f"list_chunks_by_kb p95={p95:.0f}ms"
    assert len(result) == 10000
    catalog.close()


# ── List documents ────────────────────────────────────────────────────


def test_list_documents_1000(benchmark, perf_env):
    from agentnexus.rag.store import KnowledgeBaseCatalog, _reset_knowledge_base_catalog

    _reset_knowledge_base_catalog()
    db_path = str(perf_env / "catalog.db")
    catalog = KnowledgeBaseCatalog(db_path)

    kb = KnowledgeBaseRecord(
        kb_id="perf_kb",
        namespace="perf",
        display_name="Perf",
        collection_name="perf",
    )
    catalog.upsert_knowledge_base(kb)

    now = datetime.now(timezone.utc).isoformat()
    docs = [
        SourceDocument(
            document_id=f"doc_{i:06d}",
            kb_id="perf_kb",
            source_id=f"src_{i}",
            source_uri=f"/path/doc_{i}.md",
            document_version="v1",
            content=f"Document {i} content for performance testing.",
            created_at=now,
            updated_at=now,
        )
        for i in range(1000)
    ]
    for d in docs:
        catalog.upsert_document(d)

    result = benchmark(catalog.list_documents, "perf_kb")
    assert len(result) == 1000
    catalog.close()


# ── Metadata encoding throughput ──────────────────────────────────────


def test_metadata_encode_decode(perf_env):
    from agentnexus.rag.store import _decode_metadata, _encode_metadata

    metadata = {"source": "perf", "index": 0, "tags": ["test", "perf"], "nested": {"a": 1}}
    enc = _encode_metadata(metadata)
    dec = _decode_metadata(enc)
    assert dec == metadata

    count = 1000
    metadatas = [
        {"source": "perf", "index": i, "tags": ["test", "perf"], "values": list(range(10))}
        for i in range(count)
    ]

    start = time.perf_counter()
    encoded = [_encode_metadata(m) for m in metadatas]
    encode_time = time.perf_counter() - start
    encode_p95 = encode_time * 1000 * 1.05
    assert encode_p95 < METADATA_ENCODE_1000_P95_MAX_MS, f"encode 1000 p95={encode_p95:.0f}ms"

    start = time.perf_counter()
    decoded = [_decode_metadata(e) for e in encoded]
    decode_time = time.perf_counter() - start
    decode_p95 = decode_time * 1000 * 1.05
    assert decode_p95 < METADATA_ENCODE_1000_P95_MAX_MS, f"decode 1000 p95={decode_p95:.0f}ms"

    assert len(decoded) == count
    for i, d in enumerate(decoded):
        assert d["index"] == i


# ── Knowledge base listing ────────────────────────────────────────────


def test_list_knowledge_bases(benchmark, perf_env):
    from agentnexus.rag.store import KnowledgeBaseCatalog, _reset_knowledge_base_catalog

    _reset_knowledge_base_catalog()
    db_path = str(perf_env / "catalog.db")
    catalog = KnowledgeBaseCatalog(db_path)

    for i in range(50):
        rec = KnowledgeBaseRecord(
            kb_id=f"kb_{i:03d}",
            namespace=f"ns_{i:03d}",
            display_name=f"KB {i:03d}",
            collection_name=f"coll_{i:03d}",
        )
        catalog.upsert_knowledge_base(rec)

    result = benchmark(catalog.list_knowledge_bases)
    assert len(result) == 50
    catalog.close()
