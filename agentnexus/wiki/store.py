"""Wiki storage layer — SQLite tables for wiki pages, statements, definitions, review queue.

Uses the same database as KnowledgeBaseCatalog (rag_catalog.db) via schema migration v2.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from typing import Any

from agentnexus.core.config import get_settings

from .models import (
    CanonicalDefinition,
    ConfidenceLevel,
    DefinitionEntry,
    ReviewItem,
    ReviewPriority,
    ReviewStatus,
    WikiPage,
    WikiStatement,
)

logger = logging.getLogger(__name__)

WIKI_SCHEMA = """
CREATE TABLE IF NOT EXISTS wiki_pages (
    page_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    page_type TEXT NOT NULL DEFAULT 'concept',
    content TEXT NOT NULL DEFAULT '',
    confidence TEXT NOT NULL DEFAULT 'high',
    flags_json TEXT NOT NULL DEFAULT '[]',
    source_namespace TEXT NOT NULL DEFAULT '',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_wiki_pages_source_namespace ON wiki_pages(source_namespace);
CREATE INDEX IF NOT EXISTS idx_wiki_pages_confidence ON wiki_pages(confidence);

CREATE TABLE IF NOT EXISTS wiki_statements (
    statement_id TEXT PRIMARY KEY,
    page_id TEXT NOT NULL,
    text TEXT NOT NULL,
    synthesis_level TEXT NOT NULL DEFAULT 'synthesis',
    source_chunk_ids_json TEXT NOT NULL DEFAULT '[]',
    canonical_term TEXT,
    verified_synthesis_level TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (page_id) REFERENCES wiki_pages(page_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_wiki_statements_page_id ON wiki_statements(page_id);
CREATE INDEX IF NOT EXISTS idx_wiki_statements_canonical_term ON wiki_statements(canonical_term);

CREATE TABLE IF NOT EXISTS wiki_canonical_definitions (
    page_id TEXT NOT NULL,
    term TEXT NOT NULL,
    definitions_json TEXT NOT NULL DEFAULT '[]',
    consensus TEXT,
    divergence REAL NOT NULL DEFAULT 0.0,
    last_recalculated TEXT NOT NULL,
    PRIMARY KEY (page_id, term),
    FOREIGN KEY (page_id) REFERENCES wiki_pages(page_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS wiki_review_queue (
    item_id TEXT PRIMARY KEY,
    priority INTEGER NOT NULL DEFAULT 3,
    page_id TEXT NOT NULL,
    statement_id TEXT,
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    deadline TEXT NOT NULL,
    resolved_at TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_wiki_review_status ON wiki_review_queue(status);
CREATE INDEX IF NOT EXISTS idx_wiki_review_priority ON wiki_review_queue(priority);

CREATE TABLE IF NOT EXISTS wiki_dependency_graph (
    source_page_id TEXT NOT NULL,
    dependent_page_id TEXT NOT NULL,
    relation_type TEXT NOT NULL DEFAULT 'references',
    created_at TEXT NOT NULL,
    PRIMARY KEY (source_page_id, dependent_page_id)
);
CREATE INDEX IF NOT EXISTS idx_wiki_dep_source ON wiki_dependency_graph(source_page_id);
CREATE INDEX IF NOT EXISTS idx_wiki_dep_dependent ON wiki_dependency_graph(dependent_page_id);

CREATE TABLE IF NOT EXISTS wiki_calibration (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thresholds_json TEXT NOT NULL,
    confusion_matrix_json TEXT NOT NULL DEFAULT '{}',
    sample_size INTEGER NOT NULL DEFAULT 0,
    calibrated_at TEXT NOT NULL
);
"""

_catalog_instances: dict[str, "WikiStore"] = {}


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _encode_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True)


def _decode_json(payload: str | None) -> Any:
    if not payload:
        return {}
    return json.loads(payload)


def _encode_definitions(definitions: list[DefinitionEntry]) -> str:
    return _encode_json(
        [{"text": d.text, "source_chunk_id": d.source_chunk_id, "confidence": d.confidence} for d in definitions]
    )


def _decode_definitions(payload: str | None) -> list[DefinitionEntry]:
    if not payload:
        return []
    items = json.loads(payload)
    return [DefinitionEntry(text=i["text"], source_chunk_id=i["source_chunk_id"], confidence=i["confidence"]) for i in items]


def _page_from_row(row, statements: list[WikiStatement] | None = None) -> WikiPage:
    return WikiPage(
        page_id=row["page_id"],
        title=row["title"],
        page_type=row["page_type"],
        content=row["content"],
        statements=statements or [],
        confidence=row["confidence"],
        flags=json.loads(row["flags_json"]) if row["flags_json"] else [],
        source_namespace=row["source_namespace"],
        metadata=_decode_json(row["metadata_json"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _statement_from_row(row) -> WikiStatement:
    return WikiStatement(
        statement_id=row["statement_id"],
        page_id=row["page_id"],
        text=row["text"],
        synthesis_level=row["synthesis_level"],
        source_chunk_ids=json.loads(row["source_chunk_ids_json"]) if row["source_chunk_ids_json"] else [],
        canonical_term=row["canonical_term"],
        verified_synthesis_level=row["verified_synthesis_level"],
        metadata=_decode_json(row["metadata_json"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class WikiStore:
    """Thread-safe SQLite storage for wiki pages, statements, definitions, and review queue."""

    def __init__(self, db_path: str | None = None):
        settings = get_settings()
        self._db_path = db_path or settings.rag_catalog_db_path
        self._conn = None
        self._lock = threading.RLock()

    def _get_conn(self):
        if self._conn is None:
            import sqlite3
            from pathlib import Path

            Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA busy_timeout=5000")
            self._conn.row_factory = sqlite3.Row
            self._ensure_wiki_schema()
        return self._conn

    def _ensure_wiki_schema(self):
        """Create wiki tables if they don't exist (idempotent)."""
        with self._lock:
            self._conn.executescript(WIKI_SCHEMA)
            self._conn.commit()

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    # ── Wiki Pages ──────────────────────────────────────────────────

    def upsert_page(self, page: WikiPage):
        conn = self._get_conn()
        now = _utc_now()
        created_at = page.created_at or now
        updated_at = now
        with self._lock:
            conn.execute(
                """
                INSERT INTO wiki_pages (
                    page_id, title, page_type, content, confidence,
                    flags_json, source_namespace, metadata_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(page_id) DO UPDATE SET
                    title = excluded.title,
                    page_type = excluded.page_type,
                    content = excluded.content,
                    confidence = excluded.confidence,
                    flags_json = excluded.flags_json,
                    source_namespace = excluded.source_namespace,
                    metadata_json = excluded.metadata_json,
                    updated_at = excluded.updated_at
                """,
                (
                    page.page_id, page.title, page.page_type, page.content,
                    page.confidence, _encode_json(page.flags),
                    page.source_namespace, _encode_json(page.metadata),
                    created_at, updated_at,
                ),
            )
            conn.commit()

    def get_page(self, page_id: str, include_statements: bool = True) -> WikiPage | None:
        conn = self._get_conn()
        row = conn.execute("SELECT * FROM wiki_pages WHERE page_id = ?", (page_id,)).fetchone()
        if row is None:
            return None
        statements = self.list_statements(page_id) if include_statements else []
        page = _page_from_row(row, statements)
        page.canonical_definitions = self._load_definitions(page_id)
        return page

    def list_pages(self, source_namespace: str = "", limit: int = 100) -> list[WikiPage]:
        conn = self._get_conn()
        if source_namespace:
            rows = conn.execute(
                "SELECT * FROM wiki_pages WHERE source_namespace = ? ORDER BY title ASC LIMIT ?",
                (source_namespace, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM wiki_pages ORDER BY title ASC LIMIT ?", (limit,)
            ).fetchall()
        return [_page_from_row(row) for row in rows]

    def delete_page(self, page_id: str):
        conn = self._get_conn()
        with self._lock:
            conn.execute("DELETE FROM wiki_pages WHERE page_id = ?", (page_id,))
            conn.commit()

    def update_page_confidence(self, page_id: str, confidence: str, flag: str = ""):
        conn = self._get_conn()
        with self._lock:
            if flag:
                row = conn.execute("SELECT flags_json FROM wiki_pages WHERE page_id = ?", (page_id,)).fetchone()
                flags = json.loads(row["flags_json"]) if row and row["flags_json"] else []
                if flag not in flags:
                    flags.append(flag)
                conn.execute(
                    "UPDATE wiki_pages SET confidence = ?, flags_json = ?, updated_at = ? WHERE page_id = ?",
                    (confidence, _encode_json(flags), _utc_now(), page_id),
                )
            else:
                conn.execute(
                    "UPDATE wiki_pages SET confidence = ?, updated_at = ? WHERE page_id = ?",
                    (confidence, _utc_now(), page_id),
                )
            conn.commit()

    # ── Wiki Statements ─────────────────────────────────────────────

    def upsert_statement(self, stmt: WikiStatement):
        conn = self._get_conn()
        now = _utc_now()
        created_at = stmt.created_at or now
        with self._lock:
            conn.execute(
                """
                INSERT INTO wiki_statements (
                    statement_id, page_id, text, synthesis_level,
                    source_chunk_ids_json, canonical_term,
                    verified_synthesis_level, metadata_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(statement_id) DO UPDATE SET
                    page_id = excluded.page_id,
                    text = excluded.text,
                    synthesis_level = excluded.synthesis_level,
                    source_chunk_ids_json = excluded.source_chunk_ids_json,
                    canonical_term = excluded.canonical_term,
                    verified_synthesis_level = excluded.verified_synthesis_level,
                    metadata_json = excluded.metadata_json,
                    updated_at = excluded.updated_at
                """,
                (
                    stmt.statement_id, stmt.page_id, stmt.text,
                    stmt.synthesis_level, _encode_json(stmt.source_chunk_ids),
                    stmt.canonical_term, stmt.verified_synthesis_level,
                    _encode_json(stmt.metadata), created_at, now,
                ),
            )
            conn.commit()

    def upsert_statements(self, stmts: list[WikiStatement]):
        for stmt in stmts:
            self.upsert_statement(stmt)

    def get_statement(self, statement_id: str) -> WikiStatement | None:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM wiki_statements WHERE statement_id = ?", (statement_id,)
        ).fetchone()
        return _statement_from_row(row) if row else None

    def list_statements(self, page_id: str) -> list[WikiStatement]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM wiki_statements WHERE page_id = ? ORDER BY created_at ASC",
            (page_id,),
        ).fetchall()
        return [_statement_from_row(row) for row in rows]

    def find_statements_by_chunks(self, chunk_ids: list[str]) -> list[WikiStatement]:
        """Find all statements that reference any of the given chunk IDs."""
        if not chunk_ids:
            return []
        conn = self._get_conn()
        # SQLite JSON: check if any source_chunk_ids element matches
        # We fetch all and filter in Python for portability
        rows = conn.execute("SELECT * FROM wiki_statements").fetchall()
        results = []
        chunk_set = set(chunk_ids)
        for row in rows:
            stmt = _statement_from_row(row)
            if chunk_set & set(stmt.source_chunk_ids):
                results.append(stmt)
        return results

    def update_statement_synthesis_level(self, statement_id: str, verified_level: str):
        conn = self._get_conn()
        with self._lock:
            conn.execute(
                "UPDATE wiki_statements SET verified_synthesis_level = ?, updated_at = ? WHERE statement_id = ?",
                (verified_level, _utc_now(), statement_id),
            )
            conn.commit()

    # ── Canonical Definitions ───────────────────────────────────────

    def upsert_canonical_definition(self, page_id: str, term: str, definition: CanonicalDefinition):
        conn = self._get_conn()
        with self._lock:
            conn.execute(
                """
                INSERT INTO wiki_canonical_definitions (
                    page_id, term, definitions_json, consensus,
                    divergence, last_recalculated
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(page_id, term) DO UPDATE SET
                    definitions_json = excluded.definitions_json,
                    consensus = excluded.consensus,
                    divergence = excluded.divergence,
                    last_recalculated = excluded.last_recalculated
                """,
                (
                    page_id, term, _encode_definitions(definition.definitions),
                    definition.consensus, definition.divergence,
                    definition.last_recalculated,
                ),
            )
            conn.commit()

    def _load_definitions(self, page_id: str) -> dict[str, CanonicalDefinition]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM wiki_canonical_definitions WHERE page_id = ?", (page_id,)
        ).fetchall()
        result = {}
        for row in rows:
            result[row["term"]] = CanonicalDefinition(
                definitions=_decode_definitions(row["definitions_json"]),
                consensus=row["consensus"],
                divergence=row["divergence"],
                last_recalculated=row["last_recalculated"],
            )
        return result

    # ── Dependency Graph ────────────────────────────────────────────

    def add_dependency(self, source_page_id: str, dependent_page_id: str, relation_type: str = "references"):
        conn = self._get_conn()
        with self._lock:
            conn.execute(
                """
                INSERT OR IGNORE INTO wiki_dependency_graph (
                    source_page_id, dependent_page_id, relation_type, created_at
                ) VALUES (?, ?, ?, ?)
                """,
                (source_page_id, dependent_page_id, relation_type, _utc_now()),
            )
            conn.commit()

    def list_dependents(self, page_id: str) -> list[str]:
        """Get page IDs that depend on the given page."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT dependent_page_id FROM wiki_dependency_graph WHERE source_page_id = ?",
            (page_id,),
        ).fetchall()
        return [row["dependent_page_id"] for row in rows]

    def list_dependencies(self, page_id: str) -> list[str]:
        """Get page IDs that the given page depends on."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT source_page_id FROM wiki_dependency_graph WHERE dependent_page_id = ?",
            (page_id,),
        ).fetchall()
        return [row["source_page_id"] for row in rows]

    def remove_dependencies(self, page_id: str):
        conn = self._get_conn()
        with self._lock:
            conn.execute(
                "DELETE FROM wiki_dependency_graph WHERE source_page_id = ? OR dependent_page_id = ?",
                (page_id, page_id),
            )
            conn.commit()

    # ── Review Queue ────────────────────────────────────────────────

    def add_review_item(self, item: ReviewItem):
        conn = self._get_conn()
        now = _utc_now()
        with self._lock:
            conn.execute(
                """
                INSERT INTO wiki_review_queue (
                    item_id, priority, page_id, statement_id,
                    description, status, deadline, resolved_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(item_id) DO UPDATE SET
                    priority = excluded.priority,
                    description = excluded.description,
                    status = excluded.status,
                    deadline = excluded.deadline,
                    resolved_at = excluded.resolved_at
                """,
                (
                    item.item_id, item.priority, item.page_id,
                    item.statement_id, item.description, item.status,
                    item.deadline, item.resolved_at, item.created_at or now,
                ),
            )
            conn.commit()

    def list_review_items(self, status: str = "pending", limit: int = 50) -> list[ReviewItem]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM wiki_review_queue WHERE status = ? ORDER BY priority ASC, created_at ASC LIMIT ?",
            (status, limit),
        ).fetchall()
        return [
            ReviewItem(
                item_id=row["item_id"],
                priority=row["priority"],
                page_id=row["page_id"],
                statement_id=row["statement_id"],
                description=row["description"],
                status=row["status"],
                deadline=row["deadline"],
                resolved_at=row["resolved_at"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def resolve_review_item(self, item_id: str, status: str = "resolved"):
        conn = self._get_conn()
        with self._lock:
            conn.execute(
                "UPDATE wiki_review_queue SET status = ?, resolved_at = ? WHERE item_id = ?",
                (status, _utc_now(), item_id),
            )
            conn.commit()

    def get_overdue_review_items(self) -> list[ReviewItem]:
        """Get pending items past their deadline for auto-degradation."""
        conn = self._get_conn()
        now = _utc_now()
        rows = conn.execute(
            "SELECT * FROM wiki_review_queue WHERE status = 'pending' AND deadline < ? ORDER BY priority ASC",
            (now,),
        ).fetchall()
        return [
            ReviewItem(
                item_id=row["item_id"],
                priority=row["priority"],
                page_id=row["page_id"],
                statement_id=row["statement_id"],
                description=row["description"],
                status=row["status"],
                deadline=row["deadline"],
                resolved_at=row["resolved_at"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    # ── Calibration ─────────────────────────────────────────────────

    def save_calibration(self, thresholds: dict[str, float], confusion_matrix: dict, sample_size: int):
        conn = self._get_conn()
        with self._lock:
            conn.execute(
                "INSERT INTO wiki_calibration (thresholds_json, confusion_matrix_json, sample_size, calibrated_at) VALUES (?, ?, ?, ?)",
                (_encode_json(thresholds), _encode_json(confusion_matrix), sample_size, _utc_now()),
            )
            conn.commit()

    def get_latest_calibration(self) -> dict | None:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM wiki_calibration ORDER BY calibrated_at DESC LIMIT 1"
        ).fetchone()
        if not row:
            return None
        return {
            "thresholds": _decode_json(row["thresholds_json"]),
            "confusion_matrix": _decode_json(row["confusion_matrix_json"]),
            "sample_size": row["sample_size"],
            "calibrated_at": row["calibrated_at"],
        }

    # ── Stats ───────────────────────────────────────────────────────

    def get_stats(self, source_namespace: str = "") -> dict[str, Any]:
        conn = self._get_conn()
        ns_filter = "WHERE source_namespace = ?" if source_namespace else ""
        ns_params = (source_namespace,) if source_namespace else ()

        page_count = conn.execute(
            f"SELECT COUNT(*) as c FROM wiki_pages {ns_filter}", ns_params
        ).fetchone()["c"]

        stmt_count = conn.execute(
            f"SELECT COUNT(*) as c FROM wiki_statements s JOIN wiki_pages p ON s.page_id = p.page_id {ns_filter}",
            ns_params,
        ).fetchone()["c"]

        pending_reviews = conn.execute(
            "SELECT COUNT(*) as c FROM wiki_review_queue WHERE status = 'pending'"
        ).fetchone()["c"]

        confidence_dist = {}
        if page_count > 0:
            rows = conn.execute(
                f"SELECT confidence, COUNT(*) as c FROM wiki_pages {ns_filter} GROUP BY confidence",
                ns_params,
            ).fetchall()
            confidence_dist = {row["confidence"]: row["c"] for row in rows}

        return {
            "page_count": page_count,
            "statement_count": stmt_count,
            "pending_reviews": pending_reviews,
            "confidence_distribution": confidence_dist,
        }


def get_wiki_store(db_path: str | None = None) -> WikiStore:
    """Get or create a singleton WikiStore instance."""
    settings = get_settings()
    resolved_path = db_path or settings.rag_catalog_db_path
    if resolved_path not in _catalog_instances:
        _catalog_instances[resolved_path] = WikiStore(db_path=resolved_path)
    return _catalog_instances[resolved_path]
