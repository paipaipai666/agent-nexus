import json
import math
import sqlite3
from datetime import datetime, timezone

from agentnexus.core.config import get_settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS long_term_memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    category TEXT DEFAULT 'general',
    content TEXT NOT NULL,
    importance REAL DEFAULT 0.5,
    metadata_json TEXT DEFAULT '{}',
    chroma_id TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_ltm_session ON long_term_memories(session_id);
CREATE INDEX IF NOT EXISTS idx_ltm_category ON long_term_memories(category);
"""

LTM_COLLECTION = "long_term_memories"
_ltm_collection = None


def _get_ltm_collection():
    global _ltm_collection
    if _ltm_collection is None:
        from agentnexus.rag.chroma_client import get_chroma_client
        _ltm_collection = get_chroma_client().get_or_create_collection(
            name=LTM_COLLECTION,
            metadata={"hnsw:space": "cosine"},
        )
    return _ltm_collection


class LongTermMemory:
    def __init__(self):
        settings = get_settings()
        db_path = settings.memory_db_path
        from pathlib import Path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._migrate()
        self._conn.commit()
        self._chroma_col = None

    def _migrate(self):
        cur = self._conn.execute("PRAGMA table_info(long_term_memories)")
        cols = {r["name"] for r in cur.fetchall()}
        if "chroma_id" not in cols:
            self._conn.execute("ALTER TABLE long_term_memories ADD COLUMN chroma_id TEXT")
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_ltm_chroma_id ON long_term_memories(chroma_id)")
        if "metadata_json" not in cols:
            self._conn.execute("ALTER TABLE long_term_memories ADD COLUMN metadata_json TEXT DEFAULT '{}'")

    def _ensure_chroma(self):
        if self._chroma_col is None:
            self._chroma_col = _get_ltm_collection()

    def save(self, session_id: str, content: str, category: str = "general",
             importance: float = 0.5, metadata: dict | None = None,
             embedding: list[float] | None = None):
        cur = self._conn.execute(
            "SELECT id, importance, chroma_id FROM long_term_memories WHERE content = ? AND category = ?",
            (content, category)
        )
        existing = cur.fetchone()
        if existing:
            self._conn.execute(
                "UPDATE long_term_memories SET importance = MAX(importance, ?), created_at = datetime('now') WHERE id = ?",
                (importance, existing["id"])
            )
            chroma_id = existing["chroma_id"]
        else:
            import uuid
            chroma_id = uuid.uuid4().hex
            self._conn.execute(
                "INSERT INTO long_term_memories (session_id, category, content, importance, metadata_json, chroma_id) VALUES (?, ?, ?, ?, ?, ?)",
                (session_id, category, content, importance, json.dumps(metadata or {}, ensure_ascii=False), chroma_id)
            )

        self._conn.commit()

        if embedding:
            self._ensure_chroma()
            try:
                self._chroma_col.upsert(
                    ids=[chroma_id],
                    embeddings=[embedding],
                    documents=[content],
                    metadatas=[{"category": category, "importance": importance}],
                )
            except Exception:
                pass

    def search(self, query_embedding: list[float] | None = None, category: str | None = None,
               limit: int = 5, min_similarity: float = 0.3) -> list[dict]:
        if query_embedding is None:
            sql = "SELECT * FROM long_term_memories"
            params = []
            if category:
                sql += " WHERE category = ?"
                params.append(category)
            sql += " ORDER BY created_at DESC LIMIT ?"
            params.append(limit)
            rows = self._conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]

        self._ensure_chroma()
        where_filter = None
        if category:
            where_filter = {"category": category}

        try:
            chroma_results = self._chroma_col.query(
                query_embeddings=[query_embedding],
                n_results=limit * 3,
                where=where_filter,
            )
        except Exception:
            return self._fallback_cosine_search(query_embedding, category, limit, min_similarity)

        if not chroma_results["ids"] or not chroma_results["ids"][0]:
            return self._fallback_cosine_search(query_embedding, category, limit, min_similarity)

        chroma_ids = chroma_results["ids"][0]
        chroma_distances = chroma_results["distances"][0]
        id_sim_map = {cid: 1.0 - dist for cid, dist in zip(chroma_ids, chroma_distances)}

        placeholders = ",".join("?" for _ in chroma_ids)
        rows = self._conn.execute(
            f"SELECT * FROM long_term_memories WHERE chroma_id IN ({placeholders})",
            chroma_ids,
        ).fetchall()

        row_map = {r["chroma_id"]: r for r in rows}
        scored = []
        for cid, sim in id_sim_map.items():
            r = row_map.get(cid)
            if r is None or sim < min_similarity:
                continue
            try:
                created = datetime.fromisoformat(r["created_at"])
            except ValueError:
                created = datetime.now(timezone.utc).replace(tzinfo=None)
            age_hours = (datetime.now(timezone.utc).replace(tzinfo=None) - created).total_seconds() / 3600
            decay = 1.0 / (1.0 + age_hours / 168)
            score = sim * 0.6 + r["importance"] * 0.2 + decay * 0.2
            scored.append((score, dict(r)))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [s[1] for s in scored[:limit]]

    def _fallback_cosine_search(self, query_embedding: list[float], category: str | None,
                                 limit: int, min_similarity: float) -> list[dict]:
        sql = "SELECT * FROM long_term_memories WHERE chroma_id IS NOT NULL"
        params = []
        if category:
            sql += " AND category = ?"
            params.append(category)
        rows = self._conn.execute(sql, params).fetchall()
        if not rows:
            return []

        scored = []
        for r in rows:
            try:
                chroma_results = self._chroma_col.get(ids=[r["chroma_id"]], include=["embeddings"])
                if not chroma_results["embeddings"] or not chroma_results["embeddings"][0]:
                    continue
                vec = chroma_results["embeddings"][0]
                dot = sum(a * b for a, b in zip(query_embedding, vec))
                norm_q = math.sqrt(sum(a * a for a in query_embedding))
                norm_e = math.sqrt(sum(b * b for b in vec))
                sim = dot / (norm_q * norm_e) if norm_q and norm_e else 0.0
                if sim < min_similarity:
                    continue
            except Exception:
                continue

            try:
                created = datetime.fromisoformat(r["created_at"])
            except ValueError:
                created = datetime.now(timezone.utc).replace(tzinfo=None)
            age_hours = (datetime.now(timezone.utc).replace(tzinfo=None) - created).total_seconds() / 3600
            decay = 1.0 / (1.0 + age_hours / 168)
            score = sim * 0.6 + r["importance"] * 0.2 + decay * 0.2
            scored.append((score, dict(r)))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [s[1] for s in scored[:limit]]

    def list_recent(self, limit: int = 10) -> list[dict]:
        rows = self._conn.execute(
            "SELECT id, category, content, importance, created_at FROM long_term_memories ORDER BY created_at DESC LIMIT ?",
            (limit,)
        ).fetchall()
        return [{"id": r["id"], "category": r["category"], "content": r["content"], "importance": r["importance"], "created_at": r["created_at"]} for r in rows]

    def delete(self, memory_id: int):
        row = self._conn.execute("SELECT chroma_id FROM long_term_memories WHERE id = ?", (memory_id,)).fetchone()
        if row and row["chroma_id"]:
            try:
                self._ensure_chroma()
                self._chroma_col.delete(ids=[row["chroma_id"]])
            except Exception:
                pass
        self._conn.execute("DELETE FROM long_term_memories WHERE id = ?", (memory_id,))
        self._conn.commit()
