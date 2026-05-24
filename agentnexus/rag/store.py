import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from agentnexus.core.config import get_settings

from .models import (
    ChunkRecord,
    DocumentSection,
    IngestionRunRecord,
    KnowledgeBaseRecord,
    SourceDocument,
)

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS knowledge_bases (
    kb_id TEXT PRIMARY KEY,
    namespace TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    collection_name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_knowledge_bases_collection_name ON knowledge_bases(collection_name);

CREATE TABLE IF NOT EXISTS source_documents (
    document_id TEXT PRIMARY KEY,
    kb_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    source_uri TEXT NOT NULL,
    document_version TEXT NOT NULL,
    content TEXT NOT NULL,
    raw_text TEXT NOT NULL DEFAULT '',
    indexed_text TEXT NOT NULL DEFAULT '',
    sparse_text TEXT NOT NULL DEFAULT '',
    sections_json TEXT NOT NULL DEFAULT '[]',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (kb_id) REFERENCES knowledge_bases(kb_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_source_documents_kb_id ON source_documents(kb_id);
CREATE INDEX IF NOT EXISTS idx_source_documents_source_id ON source_documents(source_id);
CREATE INDEX IF NOT EXISTS idx_source_documents_document_version ON source_documents(document_version);

CREATE TABLE IF NOT EXISTS document_chunks (
    chunk_id TEXT PRIMARY KEY,
    kb_id TEXT NOT NULL,
    document_id TEXT NOT NULL,
    document_version TEXT NOT NULL,
    chunk_index INTEGER NOT NULL,
    text TEXT NOT NULL,
    raw_text TEXT NOT NULL DEFAULT '',
    indexed_text TEXT NOT NULL DEFAULT '',
    sparse_text TEXT NOT NULL DEFAULT '',
    section_index INTEGER,
    page_number INTEGER,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (kb_id) REFERENCES knowledge_bases(kb_id) ON DELETE CASCADE,
    FOREIGN KEY (document_id) REFERENCES source_documents(document_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_document_chunks_document_id ON document_chunks(document_id);
CREATE INDEX IF NOT EXISTS idx_document_chunks_kb_id ON document_chunks(kb_id);
CREATE INDEX IF NOT EXISTS idx_document_chunks_document_version ON document_chunks(document_version);

CREATE TABLE IF NOT EXISTS ingestion_runs (
    run_id TEXT PRIMARY KEY,
    kb_id TEXT NOT NULL,
    source_uri TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    error_message TEXT NOT NULL DEFAULT '',
    documents_seen INTEGER NOT NULL DEFAULT 0,
    chunks_written INTEGER NOT NULL DEFAULT 0,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    started_at TEXT NOT NULL,
    finished_at TEXT,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (kb_id) REFERENCES knowledge_bases(kb_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_ingestion_runs_kb_id ON ingestion_runs(kb_id);
CREATE INDEX IF NOT EXISTS idx_ingestion_runs_status ON ingestion_runs(status);
"""

_catalog_instances: dict[str, "KnowledgeBaseCatalog"] = {}


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _encode_metadata(metadata: dict | None) -> str:
    return json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True)


def _decode_metadata(payload: str | None) -> dict:
    if not payload:
        return {}
    return json.loads(payload)


def _encode_sections(sections: list[DocumentSection]) -> str:
    payload = [
        {
            "section_id": section.section_id,
            "section_index": section.section_index,
            "raw_text": section.raw_text,
            "indexed_text": section.indexed_text,
            "sparse_text": section.sparse_text,
            "metadata": section.metadata,
            "page_number": section.page_number,
        }
        for section in sections
    ]
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)



def _decode_sections(payload: str | None) -> list[DocumentSection]:
    if not payload:
        return []
    return [
        DocumentSection(
            section_id=item["section_id"],
            section_index=item["section_index"],
            raw_text=item["raw_text"],
            indexed_text=item["indexed_text"],
            sparse_text=item["sparse_text"],
            metadata=item.get("metadata") or {},
            page_number=item.get("page_number"),
        )
        for item in json.loads(payload)
    ]


class KnowledgeBaseCatalog:
    def __init__(self, db_path: str | None = None):
        settings = get_settings()
        self._db_path = db_path or settings.rag_catalog_db_path
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._migrate_schema()
        self._conn.commit()

    def _migrate_schema(self):
        source_columns = {
            row["name"] for row in self._conn.execute("PRAGMA table_info(source_documents)").fetchall()
        }
        source_migrations = {
            "raw_text": "ALTER TABLE source_documents ADD COLUMN raw_text TEXT NOT NULL DEFAULT ''",
            "indexed_text": "ALTER TABLE source_documents ADD COLUMN indexed_text TEXT NOT NULL DEFAULT ''",
            "sparse_text": "ALTER TABLE source_documents ADD COLUMN sparse_text TEXT NOT NULL DEFAULT ''",
            "sections_json": "ALTER TABLE source_documents ADD COLUMN sections_json TEXT NOT NULL DEFAULT '[]'",
        }
        for column, sql in source_migrations.items():
            if column not in source_columns:
                self._conn.execute(sql)

        chunk_columns = {
            row["name"] for row in self._conn.execute("PRAGMA table_info(document_chunks)").fetchall()
        }
        chunk_migrations = {
            "raw_text": "ALTER TABLE document_chunks ADD COLUMN raw_text TEXT NOT NULL DEFAULT ''",
            "indexed_text": "ALTER TABLE document_chunks ADD COLUMN indexed_text TEXT NOT NULL DEFAULT ''",
            "sparse_text": "ALTER TABLE document_chunks ADD COLUMN sparse_text TEXT NOT NULL DEFAULT ''",
            "section_index": "ALTER TABLE document_chunks ADD COLUMN section_index INTEGER",
            "page_number": "ALTER TABLE document_chunks ADD COLUMN page_number INTEGER",
        }
        for column, sql in chunk_migrations.items():
            if column not in chunk_columns:
                self._conn.execute(sql)

    def close(self):
        self._conn.close()

    def upsert_knowledge_base(self, record: KnowledgeBaseRecord):
        created_at = record.created_at or _utc_now()
        updated_at = record.updated_at or created_at
        self._conn.execute(
            """
            INSERT INTO knowledge_bases (
                kb_id, namespace, display_name, collection_name, description,
                metadata_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(kb_id) DO UPDATE SET
                namespace = excluded.namespace,
                display_name = excluded.display_name,
                collection_name = excluded.collection_name,
                description = excluded.description,
                metadata_json = excluded.metadata_json,
                updated_at = excluded.updated_at
            """,
            (
                record.kb_id,
                record.namespace,
                record.display_name,
                record.collection_name,
                record.description,
                _encode_metadata(record.metadata),
                created_at,
                updated_at,
            ),
        )
        self._conn.commit()

    def list_knowledge_bases(self) -> list[KnowledgeBaseRecord]:
        rows = self._conn.execute(
            "SELECT * FROM knowledge_bases ORDER BY namespace ASC"
        ).fetchall()
        return [
            KnowledgeBaseRecord(
                kb_id=row["kb_id"],
                namespace=row["namespace"],
                display_name=row["display_name"],
                collection_name=row["collection_name"],
                description=row["description"],
                metadata=_decode_metadata(row["metadata_json"]),
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
            for row in rows
        ]

    def get_knowledge_base(self, namespace: str) -> KnowledgeBaseRecord | None:
        row = self._conn.execute(
            "SELECT * FROM knowledge_bases WHERE namespace = ?",
            (namespace,),
        ).fetchone()
        if row is None:
            return None
        return KnowledgeBaseRecord(
            kb_id=row["kb_id"],
            namespace=row["namespace"],
            display_name=row["display_name"],
            collection_name=row["collection_name"],
            description=row["description"],
            metadata=_decode_metadata(row["metadata_json"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def delete_knowledge_base(self, kb_id: str):
        self._conn.execute("DELETE FROM knowledge_bases WHERE kb_id = ?", (kb_id,))
        self._conn.commit()

    def upsert_document(self, record: SourceDocument):
        self.upsert_documents([record])

    def upsert_documents(self, records: list[SourceDocument]):
        if not records:
            return
        payload = []
        for record in records:
            created_at = record.created_at or _utc_now()
            updated_at = record.updated_at or created_at
            payload.append(
                (
                    record.document_id,
                    record.kb_id,
                    record.source_id,
                    record.source_uri,
                    record.document_version,
                    record.content,
                    record.raw_text or record.content,
                    record.indexed_text or record.content,
                    record.sparse_text or record.indexed_text or record.content,
                    _encode_sections(record.sections),
                    _encode_metadata(record.metadata),
                    created_at,
                    updated_at,
                )
            )
        self._conn.executemany(
            """
            INSERT INTO source_documents (
                document_id, kb_id, source_id, source_uri, document_version,
                content, raw_text, indexed_text, sparse_text, sections_json,
                metadata_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(document_id) DO UPDATE SET
                kb_id = excluded.kb_id,
                source_id = excluded.source_id,
                source_uri = excluded.source_uri,
                document_version = excluded.document_version,
                content = excluded.content,
                raw_text = excluded.raw_text,
                indexed_text = excluded.indexed_text,
                sparse_text = excluded.sparse_text,
                sections_json = excluded.sections_json,
                metadata_json = excluded.metadata_json,
                updated_at = excluded.updated_at
            """,
            payload,
        )
        self._conn.commit()

    def list_documents(self, kb_id: str | None = None) -> list[SourceDocument]:
        sql = "SELECT * FROM source_documents"
        params: list[str] = []
        if kb_id:
            sql += " WHERE kb_id = ?"
            params.append(kb_id)
        sql += " ORDER BY source_uri ASC, created_at ASC"
        rows = self._conn.execute(sql, params).fetchall()
        return [
            SourceDocument(
                document_id=row["document_id"],
                kb_id=row["kb_id"],
                source_id=row["source_id"],
                source_uri=row["source_uri"],
                document_version=row["document_version"],
                content=row["content"],
                metadata=_decode_metadata(row["metadata_json"]),
                raw_text=row["raw_text"] or row["content"],
                indexed_text=row["indexed_text"] or row["content"],
                sparse_text=row["sparse_text"] or row["indexed_text"] or row["content"],
                sections=_decode_sections(row["sections_json"]),
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
            for row in rows
        ]

    def upsert_chunks(self, records: list[ChunkRecord]):
        if not records:
            return
        now = _utc_now()
        payload = [
            (
                record.chunk_id,
                record.kb_id,
                record.document_id,
                record.document_version,
                record.chunk_index,
                record.text,
                record.raw_text or record.text,
                record.indexed_text or record.text,
                record.sparse_text or record.indexed_text or record.text,
                record.section_index,
                record.page_number,
                _encode_metadata(record.metadata),
                record.created_at or now,
                record.updated_at or now,
            )
            for record in records
        ]
        self._conn.executemany(
            """
            INSERT INTO document_chunks (
                chunk_id, kb_id, document_id, document_version, chunk_index,
                text, raw_text, indexed_text, sparse_text, section_index,
                page_number, metadata_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(chunk_id) DO UPDATE SET
                kb_id = excluded.kb_id,
                document_id = excluded.document_id,
                document_version = excluded.document_version,
                chunk_index = excluded.chunk_index,
                text = excluded.text,
                raw_text = excluded.raw_text,
                indexed_text = excluded.indexed_text,
                sparse_text = excluded.sparse_text,
                section_index = excluded.section_index,
                page_number = excluded.page_number,
                metadata_json = excluded.metadata_json,
                updated_at = excluded.updated_at
            """,
            payload,
        )
        self._conn.commit()

    def list_chunks(self, document_id: str) -> list[ChunkRecord]:
        rows = self._conn.execute(
            "SELECT * FROM document_chunks WHERE document_id = ? ORDER BY chunk_index ASC",
            (document_id,),
        ).fetchall()
        return [
            ChunkRecord(
                chunk_id=row["chunk_id"],
                kb_id=row["kb_id"],
                document_id=row["document_id"],
                document_version=row["document_version"],
                chunk_index=row["chunk_index"],
                text=row["text"],
                metadata=_decode_metadata(row["metadata_json"]),
                raw_text=row["raw_text"] or row["text"],
                indexed_text=row["indexed_text"] or row["text"],
                sparse_text=row["sparse_text"] or row["indexed_text"] or row["text"],
                section_index=row["section_index"],
                page_number=row["page_number"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
            for row in rows
        ]

    def list_chunks_by_kb(self, kb_id: str) -> list[ChunkRecord]:
        rows = self._conn.execute(
            "SELECT * FROM document_chunks WHERE kb_id = ? ORDER BY document_id ASC, chunk_index ASC",
            (kb_id,),
        ).fetchall()
        return [
            ChunkRecord(
                chunk_id=row["chunk_id"],
                kb_id=row["kb_id"],
                document_id=row["document_id"],
                document_version=row["document_version"],
                chunk_index=row["chunk_index"],
                text=row["text"],
                metadata=_decode_metadata(row["metadata_json"]),
                raw_text=row["raw_text"] or row["text"],
                indexed_text=row["indexed_text"] or row["text"],
                sparse_text=row["sparse_text"] or row["indexed_text"] or row["text"],
                section_index=row["section_index"],
                page_number=row["page_number"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
            for row in rows
        ]

    def upsert_ingestion_run(self, record: IngestionRunRecord):
        started_at = record.started_at or _utc_now()
        updated_at = record.updated_at or started_at
        self._conn.execute(
            """
            INSERT INTO ingestion_runs (
                run_id, kb_id, source_uri, status, error_message,
                documents_seen, chunks_written, metadata_json,
                started_at, finished_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
                kb_id = excluded.kb_id,
                source_uri = excluded.source_uri,
                status = excluded.status,
                error_message = excluded.error_message,
                documents_seen = excluded.documents_seen,
                chunks_written = excluded.chunks_written,
                metadata_json = excluded.metadata_json,
                finished_at = excluded.finished_at,
                updated_at = excluded.updated_at
            """,
            (
                record.run_id,
                record.kb_id,
                record.source_uri,
                record.status,
                record.error_message,
                record.documents_seen,
                record.chunks_written,
                _encode_metadata(record.metadata),
                started_at,
                record.finished_at,
                updated_at,
            ),
        )
        self._conn.commit()

    def list_ingestion_runs(self, kb_id: str | None = None) -> list[IngestionRunRecord]:
        sql = "SELECT * FROM ingestion_runs"
        params: list[str] = []
        if kb_id:
            sql += " WHERE kb_id = ?"
            params.append(kb_id)
        sql += " ORDER BY updated_at DESC"
        rows = self._conn.execute(sql, params).fetchall()
        return [
            IngestionRunRecord(
                run_id=row["run_id"],
                kb_id=row["kb_id"],
                status=row["status"],
                source_uri=row["source_uri"],
                error_message=row["error_message"],
                documents_seen=row["documents_seen"],
                chunks_written=row["chunks_written"],
                metadata=_decode_metadata(row["metadata_json"]),
                started_at=row["started_at"],
                finished_at=row["finished_at"],
                updated_at=row["updated_at"],
            )
            for row in rows
        ]


def get_knowledge_base_catalog() -> KnowledgeBaseCatalog:
    settings = get_settings()
    db_path = settings.rag_catalog_db_path
    if db_path not in _catalog_instances:
        _catalog_instances[db_path] = KnowledgeBaseCatalog(db_path=db_path)
    return _catalog_instances[db_path]


def _reset_knowledge_base_catalog():
    for catalog in _catalog_instances.values():
        try:
            catalog.close()
        except Exception:
            pass
    _catalog_instances.clear()
