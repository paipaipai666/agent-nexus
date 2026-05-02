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
    embedding_json TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_ltm_session ON long_term_memories(session_id);
CREATE INDEX IF NOT EXISTS idx_ltm_category ON long_term_memories(category);
"""


class LongTermMemory:
    def __init__(self):
        settings = get_settings()
        db_path = settings.memory_db_path
        from pathlib import Path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def save(self, session_id: str, content: str, category: str = "general",
             importance: float = 0.5, metadata: dict | None = None,
             embedding: list[float] | None = None):
        emb_json = json.dumps(embedding) if embedding else None
        cur = self._conn.execute(
            "SELECT id, importance FROM long_term_memories WHERE content = ? AND category = ?",
            (content, category)
        )
        existing = cur.fetchone()
        if existing:
            self._conn.execute(
                "UPDATE long_term_memories SET importance = MAX(importance, ?), created_at = datetime('now') WHERE id = ?",
                (importance, existing["id"])
            )
        else:
            self._conn.execute(
                "INSERT INTO long_term_memories (session_id, category, content, importance, metadata_json, embedding_json) VALUES (?, ?, ?, ?, ?, ?)",
                (session_id, category, content, importance, json.dumps(metadata or {}, ensure_ascii=False), emb_json)
            )
        self._conn.commit()

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

        rows = self._conn.execute(
            "SELECT * FROM long_term_memories WHERE embedding_json IS NOT NULL"
        ).fetchall()

        scored = []
        for r in rows:
            vec = json.loads(r["embedding_json"])
            dot = sum(a * b for a, b in zip(query_embedding, vec))
            norm_q = math.sqrt(sum(a * a for a in query_embedding))
            norm_e = math.sqrt(sum(b * b for b in vec))
            sim = dot / (norm_q * norm_e) if norm_q and norm_e else 0.0
            if sim < min_similarity:
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
        return [{"id": r["id"], "category": r["category"], "content": r["content"][:120], "importance": r["importance"], "created_at": r["created_at"]} for r in rows]

    def delete(self, memory_id: int):
        self._conn.execute("DELETE FROM long_term_memories WHERE id = ?", (memory_id,))
        self._conn.commit()
