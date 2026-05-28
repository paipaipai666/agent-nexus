"""SQLite storage layer for the code knowledge graph.

Handles schema creation, migrations, CRUD operations for nodes/edges/files,
WAL management, and the 999-parameter workaround using temp tables.
"""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from agentnexus.codegraph.models import EdgeData, EdgeKind, NodeData

# Schema version for migrations
SCHEMA_VERSION = 1

# Batch size to stay under SQLite's 999 parameter limit
BATCH_SIZE = 900

# SQL statements for schema creation
_SCHEMA_SQL = """
-- Nodes table
CREATE TABLE IF NOT EXISTS nodes (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    qualified_name TEXT NOT NULL,
    file_path TEXT NOT NULL,
    language TEXT NOT NULL,
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    start_column INTEGER NOT NULL DEFAULT 0,
    end_column INTEGER NOT NULL DEFAULT 0,
    docstring TEXT,
    signature TEXT,
    visibility TEXT,
    is_exported INTEGER DEFAULT 0,
    is_async INTEGER DEFAULT 0,
    is_static INTEGER DEFAULT 0,
    is_abstract INTEGER DEFAULT 0,
    decorators TEXT,
    type_parameters TEXT,
    updated_at INTEGER NOT NULL
);

-- Edges table
CREATE TABLE IF NOT EXISTS edges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    target TEXT NOT NULL,
    kind TEXT NOT NULL,
    metadata TEXT,
    line INTEGER,
    col INTEGER,
    provenance TEXT DEFAULT NULL,
    FOREIGN KEY (source) REFERENCES nodes(id) ON DELETE CASCADE,
    FOREIGN KEY (target) REFERENCES nodes(id) ON DELETE CASCADE
);

-- File tracking table
CREATE TABLE IF NOT EXISTS files (
    path TEXT PRIMARY KEY,
    content_hash TEXT NOT NULL,
    language TEXT NOT NULL,
    size INTEGER NOT NULL,
    modified_at INTEGER NOT NULL,
    indexed_at INTEGER NOT NULL,
    node_count INTEGER DEFAULT 0,
    errors TEXT
);

-- Write-Ahead Log (file-level)
CREATE TABLE IF NOT EXISTS wal (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_path TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    timestamp INTEGER NOT NULL
);

-- Schema migration versions
CREATE TABLE IF NOT EXISTS schema_versions (
    version INTEGER PRIMARY KEY,
    applied_at INTEGER NOT NULL,
    description TEXT
);

-- Node indexes
CREATE INDEX IF NOT EXISTS idx_nodes_kind ON nodes(kind);
CREATE INDEX IF NOT EXISTS idx_nodes_name ON nodes(name);
CREATE INDEX IF NOT EXISTS idx_nodes_qualified_name ON nodes(qualified_name);
CREATE INDEX IF NOT EXISTS idx_nodes_file_path ON nodes(file_path);
CREATE INDEX IF NOT EXISTS idx_nodes_language ON nodes(language);
CREATE INDEX IF NOT EXISTS idx_nodes_file_line ON nodes(file_path, start_line);
CREATE INDEX IF NOT EXISTS idx_nodes_lower_name ON nodes(lower(name));

-- Edge indexes
CREATE INDEX IF NOT EXISTS idx_edges_kind ON edges(kind);
CREATE INDEX IF NOT EXISTS idx_edges_source_kind ON edges(source, kind);
CREATE INDEX IF NOT EXISTS idx_edges_target_kind ON edges(target, kind);

-- File indexes
CREATE INDEX IF NOT EXISTS idx_files_language ON files(language);
CREATE INDEX IF NOT EXISTS idx_files_modified_at ON files(modified_at);
"""

_FTS5_SQL = """
CREATE VIRTUAL TABLE IF NOT EXISTS nodes_fts USING fts5(
    id,
    name,
    qualified_name,
    docstring,
    signature,
    content='nodes',
    content_rowid='rowid'
);

-- Auto-sync triggers
CREATE TRIGGER IF NOT EXISTS nodes_ai AFTER INSERT ON nodes BEGIN
    INSERT INTO nodes_fts(rowid, id, name, qualified_name, docstring, signature)
    VALUES (NEW.rowid, NEW.id, NEW.name, NEW.qualified_name, NEW.docstring, NEW.signature);
END;

CREATE TRIGGER IF NOT EXISTS nodes_ad AFTER DELETE ON nodes BEGIN
    INSERT INTO nodes_fts(nodes_fts, rowid, id, name, qualified_name, docstring, signature)
    VALUES ('delete', OLD.rowid, OLD.id, OLD.name, OLD.qualified_name, OLD.docstring, OLD.signature);
END;

CREATE TRIGGER IF NOT EXISTS nodes_au AFTER UPDATE ON nodes BEGIN
    INSERT INTO nodes_fts(nodes_fts, rowid, id, name, qualified_name, docstring, signature)
    VALUES ('delete', OLD.rowid, OLD.id, OLD.name, OLD.qualified_name, OLD.docstring, OLD.signature);
    INSERT INTO nodes_fts(rowid, id, name, qualified_name, docstring, signature)
    VALUES (NEW.rowid, NEW.id, NEW.name, NEW.qualified_name, NEW.docstring, NEW.signature);
END;
"""


def detect_project_root() -> Path:
    """Detect project root: walk up looking for .git, fallback to cwd."""
    cwd = Path.cwd()
    for parent in [cwd, *cwd.parents]:
        if (parent / ".git").exists():
            return parent
    return cwd


def get_db_path(project_root: Path | None = None) -> Path:
    """Get the codegraph database path for a project."""
    if project_root is None:
        project_root = detect_project_root()
    return project_root / ".agentnexus" / "codegraph.db"


class CodeGraphStore:
    """SQLite-backed storage for the code knowledge graph.

    Provides CRUD for nodes, edges, file tracking, and WAL management.
    All writes are transactional. The database is created on first use.
    """

    def __init__(self, db_path: Path | str) -> None:
        self._db_path = str(db_path)
        self._conn: sqlite3.Connection | None = None

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(self._db_path)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def close(self) -> None:
        if self._conn:
            self._conn.commit()
            self._conn.close()
            self._conn = None

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def init_schema(self) -> None:
        """Create tables, indexes, and FTS5 if not present."""
        conn = self._get_conn()
        conn.executescript(_SCHEMA_SQL)
        try:
            conn.executescript(_FTS5_SQL)
        except sqlite3.OperationalError:
            pass  # FTS5 may already exist
        self._record_version(SCHEMA_VERSION, "initial schema")
        conn.commit()

    def _record_version(self, version: int, description: str) -> None:
        conn = self._get_conn()
        conn.execute(
            "INSERT OR IGNORE INTO schema_versions (version, applied_at, description) VALUES (?, ?, ?)",
            (version, int(time.time()), description),
        )

    @contextmanager
    def transaction(self):
        """Context manager for explicit transactions."""
        conn = self._get_conn()
        conn.execute("BEGIN")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    # ------------------------------------------------------------------
    # Nodes CRUD
    # ------------------------------------------------------------------

    def upsert_node(self, node: NodeData) -> None:
        """Insert or replace a single node."""
        conn = self._get_conn()
        now = int(time.time())
        conn.execute(
            """INSERT OR REPLACE INTO nodes
            (id, kind, name, qualified_name, file_path, language,
             start_line, end_line, start_column, end_column,
             docstring, signature, visibility, is_exported, is_async,
             is_static, is_abstract, decorators, type_parameters, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                node.id,
                node.kind,
                node.name,
                node.qualified_name,
                node.file_path,
                node.language,
                node.start_line,
                node.end_line,
                node.start_column,
                node.end_column,
                node.docstring,
                node.signature,
                node.visibility,
                int(node.is_exported),
                int(node.is_async),
                int(node.is_static),
                int(node.is_abstract),
                json.dumps(node.decorators) if node.decorators else None,
                json.dumps(node.type_parameters) if node.type_parameters else None,
                now,
            ),
        )

    def upsert_nodes_batch(self, nodes: list[NodeData]) -> None:
        """Insert or replace multiple nodes in a single transaction."""
        conn = self._get_conn()
        now = int(time.time())
        rows = [
            (
                n.id, n.kind, n.name, n.qualified_name, n.file_path, n.language,
                n.start_line, n.end_line, n.start_column, n.end_column,
                n.docstring, n.signature, n.visibility,
                int(n.is_exported), int(n.is_async), int(n.is_static), int(n.is_abstract),
                json.dumps(n.decorators) if n.decorators else None,
                json.dumps(n.type_parameters) if n.type_parameters else None,
                now,
            )
            for n in nodes
        ]
        conn.executemany(
            """INSERT OR REPLACE INTO nodes
            (id, kind, name, qualified_name, file_path, language,
             start_line, end_line, start_column, end_column,
             docstring, signature, visibility, is_exported, is_async,
             is_static, is_abstract, decorators, type_parameters, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )

    def get_node(self, node_id: str) -> dict | None:
        """Get a single node by ID."""
        conn = self._get_conn()
        row = conn.execute("SELECT * FROM nodes WHERE id = ?", (node_id,)).fetchone()
        return dict(row) if row else None

    def get_nodes_by_file(self, file_path: str) -> list[dict]:
        """Get all nodes belonging to a file."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM nodes WHERE file_path = ? ORDER BY start_line",
            (file_path,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_nodes_by_ids(self, node_ids: set[str]) -> list[dict]:
        """Get multiple nodes by ID (handles batching for >900 IDs)."""
        conn = self._get_conn()
        results: list[dict] = []
        id_list = list(node_ids)
        for i in range(0, len(id_list), BATCH_SIZE):
            batch = id_list[i : i + BATCH_SIZE]
            placeholders = ",".join("?" * len(batch))
            rows = conn.execute(
                f"SELECT * FROM nodes WHERE id IN ({placeholders})", batch
            ).fetchall()
            results.extend(dict(r) for r in rows)
        return results

    def get_all_node_ids(self) -> set[str]:
        """Get all node IDs in the store."""
        conn = self._get_conn()
        rows = conn.execute("SELECT id FROM nodes").fetchall()
        return {row["id"] for row in rows}

    def delete_nodes_by_file(self, file_path: str, exclude_ids: set[str] | None = None) -> int:
        """Delete nodes for a file, optionally excluding specific IDs.

        Uses a temp table to handle the 999-parameter limit.
        Returns the number of deleted nodes.
        """
        conn = self._get_conn()
        if not exclude_ids:
            cursor = conn.execute("DELETE FROM nodes WHERE file_path = ?", (file_path,))
            return cursor.rowcount

        # Use temp table for large exclude sets
        conn.execute("CREATE TEMP TABLE IF NOT EXISTS _tmp_keep_ids (id TEXT PRIMARY KEY)")
        conn.execute("DELETE FROM _tmp_keep_ids")

        id_list = list(exclude_ids)
        for i in range(0, len(id_list), BATCH_SIZE):
            batch = id_list[i : i + BATCH_SIZE]
            conn.executemany(
                "INSERT OR IGNORE INTO _tmp_keep_ids VALUES (?)",
                [(x,) for x in batch],
            )

        cursor = conn.execute(
            """DELETE FROM nodes
            WHERE file_path = ?
              AND id NOT IN (SELECT id FROM _tmp_keep_ids)""",
            (file_path,),
        )
        conn.execute("DROP TABLE IF EXISTS _tmp_keep_ids")
        return cursor.rowcount

    # ------------------------------------------------------------------
    # Edges CRUD
    # ------------------------------------------------------------------

    def upsert_edge(self, edge: EdgeData) -> None:
        """Insert an edge (always new, with auto-increment ID)."""
        conn = self._get_conn()
        conn.execute(
            """INSERT INTO edges (source, target, kind, metadata, line, col)
            VALUES (?, ?, ?, ?, ?, ?)""",
            (
                edge.source,
                edge.target,
                edge.kind,
                json.dumps(edge.metadata) if edge.metadata else None,
                edge.line,
                edge.col,
            ),
        )

    def upsert_edges_batch(self, edges: list[EdgeData]) -> None:
        """Insert multiple edges."""
        conn = self._get_conn()
        rows = [
            (
                e.source, e.target, e.kind,
                json.dumps(e.metadata) if e.metadata else None,
                e.line, e.col,
            )
            for e in edges
        ]
        conn.executemany(
            """INSERT INTO edges (source, target, kind, metadata, line, col)
            VALUES (?, ?, ?, ?, ?, ?)""",
            rows,
        )

    def delete_edges_by_file(self, file_path: str) -> int:
        """Delete all edges where source or target belongs to a file's nodes."""
        conn = self._get_conn()
        cursor = conn.execute(
            """DELETE FROM edges
            WHERE source IN (SELECT id FROM nodes WHERE file_path = ?)
               OR target IN (SELECT id FROM nodes WHERE file_path = ?)""",
            (file_path, file_path),
        )
        return cursor.rowcount

    def get_edges_from(self, node_id: str, kind: str | None = None) -> list[dict]:
        """Get edges originating from a node."""
        conn = self._get_conn()
        if kind:
            rows = conn.execute(
                "SELECT * FROM edges WHERE source = ? AND kind = ?",
                (node_id, kind),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM edges WHERE source = ?", (node_id,)
            ).fetchall()
        return [dict(r) for r in rows]

    def get_edges_to(self, node_id: str, kind: str | None = None) -> list[dict]:
        """Get edges targeting a node."""
        conn = self._get_conn()
        if kind:
            rows = conn.execute(
                "SELECT * FROM edges WHERE target = ? AND kind = ?",
                (node_id, kind),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM edges WHERE target = ?", (node_id,)
            ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # File tracking
    # ------------------------------------------------------------------

    def upsert_file(
        self,
        path: str,
        content_hash: str,
        language: str,
        size: int,
        modified_at: int,
        node_count: int,
        errors: list[str] | None = None,
    ) -> None:
        """Insert or update file tracking record."""
        conn = self._get_conn()
        now = int(time.time())
        conn.execute(
            """INSERT OR REPLACE INTO files
            (path, content_hash, language, size, modified_at, indexed_at, node_count, errors)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (path, content_hash, language, size, modified_at, now, node_count,
             json.dumps(errors) if errors else None),
        )

    def get_file(self, path: str) -> dict | None:
        """Get file tracking record."""
        conn = self._get_conn()
        row = conn.execute("SELECT * FROM files WHERE path = ?", (path,)).fetchone()
        return dict(row) if row else None

    def get_all_files(self) -> list[dict]:
        """Get all tracked files."""
        conn = self._get_conn()
        rows = conn.execute("SELECT * FROM files ORDER BY path").fetchall()
        return [dict(r) for r in rows]

    def delete_file(self, path: str) -> None:
        """Delete file tracking record."""
        conn = self._get_conn()
        conn.execute("DELETE FROM files WHERE path = ?", (path,))

    def update_file_errors(self, path: str, errors: list[str]) -> None:
        """Update parse errors for a file."""
        conn = self._get_conn()
        conn.execute(
            "UPDATE files SET errors = ? WHERE path = ?",
            (json.dumps(errors), path),
        )

    # ------------------------------------------------------------------
    # WAL management
    # ------------------------------------------------------------------

    def wal_begin(self, file_path: str, content_hash: str) -> int:
        """Record WAL entry for crash recovery."""
        conn = self._get_conn()
        cursor = conn.execute(
            """INSERT INTO wal (file_path, content_hash, status, timestamp)
            VALUES (?, ?, 'in_progress', ?)""",
            (file_path, content_hash, int(time.time())),
        )
        conn.commit()
        return cursor.lastrowid  # type: ignore[return-value]

    def wal_clear(self, file_path: str) -> None:
        """Clear WAL entries for a file after successful processing."""
        conn = self._get_conn()
        conn.execute("DELETE FROM wal WHERE file_path = ?", (file_path,))
        conn.commit()

    def wal_clear_all(self) -> None:
        """Clear all WAL entries (used by --force full rebuild)."""
        conn = self._get_conn()
        conn.execute("DELETE FROM wal")
        conn.commit()

    def wal_get_pending(self) -> list[dict]:
        """Get all in-progress WAL entries for crash recovery."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM wal WHERE status = 'in_progress' ORDER BY timestamp"
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Graph queries
    # ------------------------------------------------------------------

    def get_callers(self, node_id: str, depth: int = 2) -> list[dict]:
        """Find nodes that call the given node (up to depth levels)."""
        conn = self._get_conn()
        results: list[dict] = []
        visited: set[str] = set()
        frontier = [node_id]

        for _ in range(depth):
            if not frontier:
                break
            placeholders = ",".join("?" * len(frontier))
            params: list = list(frontier) + [EdgeKind.CALLS]
            exclude_clause = ""
            if visited:
                exclude_placeholders = ",".join("?" * len(visited))
                exclude_clause = f"AND n.id NOT IN ({exclude_placeholders})"
                params.extend(visited)

            rows = conn.execute(
                f"""SELECT DISTINCT n.*, e.kind as edge_kind, e.line as edge_line
                FROM edges e
                JOIN nodes n ON n.id = e.source
                WHERE e.target IN ({placeholders})
                  AND e.kind = ?
                  {exclude_clause}
                """,
                params,
            ).fetchall()
            next_frontier: list[str] = []
            for row in rows:
                d = dict(row)
                if d["id"] not in visited:
                    visited.add(d["id"])
                    results.append(d)
                    next_frontier.append(d["id"])
            frontier = next_frontier

        return results

    def get_callees(self, node_id: str, depth: int = 2) -> list[dict]:
        """Find nodes called by the given node (up to depth levels)."""
        conn = self._get_conn()
        results: list[dict] = []
        visited: set[str] = set()
        frontier = [node_id]

        for _ in range(depth):
            if not frontier:
                break
            placeholders = ",".join("?" * len(frontier))
            params: list = list(frontier) + [EdgeKind.CALLS]
            exclude_clause = ""
            if visited:
                exclude_placeholders = ",".join("?" * len(visited))
                exclude_clause = f"AND n.id NOT IN ({exclude_placeholders})"
                params.extend(visited)

            rows = conn.execute(
                f"""SELECT DISTINCT n.*, e.kind as edge_kind, e.line as edge_line
                FROM edges e
                JOIN nodes n ON n.id = e.target
                WHERE e.source IN ({placeholders})
                  AND e.kind = ?
                  {exclude_clause}
                """,
                params,
            ).fetchall()
            next_frontier = []
            for row in rows:
                d = dict(row)
                if d["id"] not in visited:
                    visited.add(d["id"])
                    results.append(d)
                    next_frontier.append(d["id"])
            frontier = next_frontier

        return results

    def get_inheritance_tree(self, class_id: str) -> list[dict]:
        """Get the inheritance hierarchy for a class."""
        conn = self._get_conn()
        rows = conn.execute(
            """SELECT n.*, e.kind as edge_kind
            FROM edges e
            JOIN nodes n ON n.id = e.target
            WHERE e.source = ? AND e.kind = ?""",
            (class_id, EdgeKind.INHERITS),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_imports(self, module_path: str) -> list[dict]:
        """Get import relationships for a file."""
        conn = self._get_conn()
        rows = conn.execute(
            """SELECT n.*, e.kind as edge_kind, e.metadata as edge_metadata
            FROM edges e
            JOIN nodes n ON n.id = e.target
            WHERE e.source = ? AND e.kind = ?""",
            (f"file:{module_path}", EdgeKind.IMPORTS),
        ).fetchall()
        return [dict(r) for r in rows]

    def find_affected_files(self, old_node_ids: set[str], current_file: str) -> set[str]:
        """Find files with edges pointing to nodes about to be deleted.

        Must be called before the SQLite transaction deletes the old nodes.
        """
        if not old_node_ids:
            return set()

        conn = self._get_conn()
        affected: set[str] = set()
        id_list = list(old_node_ids)

        for i in range(0, len(id_list), BATCH_SIZE):
            batch = id_list[i : i + BATCH_SIZE]
            placeholders = ",".join("?" * len(batch))
            rows = conn.execute(
                f"""SELECT DISTINCT n.file_path
                FROM edges e
                JOIN nodes n ON (n.id = e.source OR n.id = e.target)
                WHERE (e.source IN ({placeholders}) OR e.target IN ({placeholders}))
                  AND n.file_path != ?""",
                batch + batch + [current_file],
            ).fetchall()
            affected.update(row["file_path"] for row in rows)

        return affected

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def get_stats(self) -> dict[str, Any]:
        """Get summary statistics."""
        conn = self._get_conn()
        node_count = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        edge_count = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
        file_count = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        last_updated_row = conn.execute(
            "SELECT MAX(updated_at) FROM nodes"
        ).fetchone()
        last_updated = last_updated_row[0] if last_updated_row else None

        kind_counts = {}
        for row in conn.execute(
            "SELECT kind, COUNT(*) as cnt FROM nodes GROUP BY kind"
        ).fetchall():
            kind_counts[row["kind"]] = row["cnt"]

        edge_kind_counts = {}
        for row in conn.execute(
            "SELECT kind, COUNT(*) as cnt FROM edges GROUP BY kind"
        ).fetchall():
            edge_kind_counts[row["kind"]] = row["cnt"]

        return {
            "node_count": node_count,
            "edge_count": edge_count,
            "file_count": file_count,
            "last_updated": last_updated,
            "node_kinds": kind_counts,
            "edge_kinds": edge_kind_counts,
        }

    # ------------------------------------------------------------------
    # Full clear (for --force rebuild)
    # ------------------------------------------------------------------

    def clear_all(self) -> None:
        """Delete all data. Used by --force full rebuild."""
        conn = self._get_conn()
        conn.execute("DELETE FROM edges")
        conn.execute("DELETE FROM nodes")
        conn.execute("DELETE FROM files")
        conn.commit()


__all__ = ["CodeGraphStore", "get_db_path", "detect_project_root", "BATCH_SIZE", "SCHEMA_VERSION"]
