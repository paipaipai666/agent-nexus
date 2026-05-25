import json
import logging
import math
import sqlite3
import threading
from datetime import datetime, timezone

from agentnexus.core.config import get_settings

logger = logging.getLogger(__name__)

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


_ltm_instances: dict[str, "LongTermMemory"] = {}


def get_long_term_memory():
    """Return a singleton LongTermMemory instance keyed by db_path."""
    settings = get_settings()
    db_path = settings.memory_db_path
    if db_path not in _ltm_instances:
        _ltm_instances[db_path] = LongTermMemory()
    return _ltm_instances[db_path]


def _reset_long_term_memory():
    """Close connections and clear the singleton cache. Used by tests."""
    for inst in _ltm_instances.values():
        try:
            inst._conn.close()
        except Exception:
            pass
    _ltm_instances.clear()


class LongTermMemory:
    def __init__(self):
        settings = get_settings()
        db_path = settings.memory_db_path
        self._max_memories = settings.max_memories
        self._ttl_days = settings.memory_ttl_days
        from pathlib import Path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._migrate()
        self._conn.commit()
        self._chroma_col = None
        self._write_counter: int = 0
        self._lock = threading.RLock()

    def _migrate(self):
        cur = self._conn.execute("PRAGMA table_info(long_term_memories)")
        cols = {r["name"] for r in cur.fetchall()}
        if "chroma_id" not in cols:
            self._conn.execute("ALTER TABLE long_term_memories ADD COLUMN chroma_id TEXT")
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_ltm_chroma_id ON long_term_memories(chroma_id)")
        if "metadata_json" not in cols:
            self._conn.execute("ALTER TABLE long_term_memories ADD COLUMN metadata_json TEXT DEFAULT '{}'")

    @property
    def write_counter(self) -> int:
        return self._write_counter

    def _ensure_chroma(self):
        if self._chroma_col is None:
            self._chroma_col = _get_ltm_collection()

    def save(self, session_id: str, content: str, category: str = "general",
             importance: float = 0.5, metadata: dict | None = None,
             embedding: list[float] | None = None):
        with self._lock:
            cur = self._conn.execute(
            "SELECT id, importance, chroma_id FROM long_term_memories WHERE content = ? AND category = ?",
            (content, category)
        )
        existing = cur.fetchone()
        if existing:
            self._conn.execute(
                "UPDATE long_term_memories "
                "SET importance = MAX(importance, ?), created_at = datetime('now') "
                "WHERE id = ?",
                (importance, existing["id"]),
            )
            chroma_id = existing["chroma_id"]
        else:
            import uuid
            chroma_id = uuid.uuid4().hex
            self._conn.execute(
                "INSERT INTO long_term_memories "
                "(session_id, category, content, importance, metadata_json, chroma_id) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    session_id,
                    category,
                    content,
                    importance,
                    json.dumps(metadata or {}, ensure_ascii=False),
                    chroma_id,
                ),
            )
            self._write_counter += 1

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
            except Exception as e:
                logger.warning("ChromaDB upsert failed for memory %s: %s", chroma_id, e)

        count_row = self._conn.execute("SELECT COUNT(*) as cnt FROM long_term_memories").fetchone()
        if count_row["cnt"] > self._max_memories:
            self._evict_if_needed()

    def _evict_if_needed(self):
        """Evict oldest/lowest-importance memories when over max_memories."""
        with self._lock:
            self._compact_low_score()

            count_row = self._conn.execute("SELECT COUNT(*) as cnt FROM long_term_memories").fetchone()
            current = count_row["cnt"]
            if current <= self._max_memories:
                return

            excess = current - self._max_memories
            # Fetch chroma_ids for evicted rows before deleting
            to_evict = self._conn.execute(
                "SELECT id, chroma_id FROM long_term_memories ORDER BY importance ASC, created_at ASC LIMIT ?",
                (excess,)
            ).fetchall()
            chroma_ids = [r["chroma_id"] for r in to_evict if r["chroma_id"]]
            ids_to_delete = [r["id"] for r in to_evict]

            # Delete from ChromaDB first
            if chroma_ids:
                try:
                    self._ensure_chroma()
                    self._chroma_col.delete(ids=chroma_ids)
                except Exception as e:
                    logger.warning("ChromaDB eviction failed: %s", e)

            # Then delete from SQLite
            placeholders = ",".join("?" for _ in ids_to_delete)
            self._conn.execute(
                f"DELETE FROM long_term_memories WHERE id IN ({placeholders})", ids_to_delete
            )
            self._conn.commit()
            logger.info("Evicted %d memories (limit: %d)", len(ids_to_delete), self._max_memories)

            self._cleanup_expired()

    def _compact_low_score(self):
        """Compress medium-importance memories: merge same-category entries >5 into one.

        Low importance (<0.3) is left for eviction. High (>0.6) is preserved.
        Medium (0.3-0.6) with >5 entries in a category → merge oldest into a single summary.
        """
        rows = self._conn.execute(
            "SELECT category, COUNT(*) as cnt FROM long_term_memories "
            "WHERE importance BETWEEN 0.3 AND 0.6 "
            "GROUP BY category HAVING cnt > 5"
        ).fetchall()

        for row in rows:
            category = row["category"]
            # Get the oldest medium-score entries in this category (keep newest 3)
            entries = self._conn.execute(
                "SELECT id, content, importance FROM long_term_memories "
                "WHERE category = ? AND importance BETWEEN 0.3 AND 0.6 "
                "ORDER BY created_at ASC",
                (category,)
            ).fetchall()

            if len(entries) <= 5:
                continue

            # Keep the newest 3, merge the rest into one summary
            to_merge = entries[:-3]
            merged_content = "; ".join(e["content"] for e in to_merge)[:1000]
            avg_importance = sum(e["importance"] for e in to_merge) / len(to_merge)

            # Delete old entries
            for e in to_merge:
                self.delete(e["id"])

            # Save merged summary (without embedding — will be re-embedded on next update)
            self.save(
                session_id="system",
                content=f"[合并记忆] {merged_content}",
                category=category,
                importance=min(avg_importance, 0.6),
            )
            logger.info("Compacted %d medium-score '%s' memories into one", len(to_merge), category)

    def _cleanup_expired(self):
        """Delete memories older than memory_ttl_days (both SQLite and ChromaDB)."""
        expired = self._conn.execute(
            "SELECT chroma_id FROM long_term_memories WHERE "
            "datetime(created_at) < datetime('now', ?)",
            (f"-{self._ttl_days} days",)
        ).fetchall()
        chroma_ids = [r["chroma_id"] for r in expired if r["chroma_id"]]

        if chroma_ids:
            try:
                self._ensure_chroma()
                self._chroma_col.delete(ids=chroma_ids)
            except Exception as e:
                logger.warning("ChromaDB cleanup of expired memories failed: %s", e)

        self._conn.execute(
            "DELETE FROM long_term_memories WHERE "
            "datetime(created_at) < datetime('now', ?)",
            (f"-{self._ttl_days} days",)
        )
        removed = self._conn.total_changes
        if removed:
            self._conn.commit()
            logger.info("Cleaned up %d expired memories (>%d days)", removed, self._ttl_days)

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
        except Exception as e:
            logger.warning("ChromaDB query failed, falling back to cosine search: %s", e)
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
        results = []
        for s in scored[:limit]:
            d = s[1]
            d["_score"] = round(s[0], 3)
            results.append(d)
        return results

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

        # Batch fetch all embeddings in a single ChromaDB call
        all_ids = [r["chroma_id"] for r in rows]
        try:
            chroma_results = self._chroma_col.get(ids=all_ids, include=["embeddings"])
        except Exception as e:
            logger.warning("ChromaDB batch get failed in fallback search: %s", e)
            return []

        # Build id -> embedding map from batch results
        id_vec_map: dict[str, list[float]] = {}
        result_ids = chroma_results.get("ids", [])
        raw_embs = chroma_results.get("embeddings")
        result_embeddings = raw_embs if raw_embs is not None else []
        for cid, emb in zip(result_ids, result_embeddings):
            if emb is not None:
                id_vec_map[cid] = emb

        # Precompute query norm once
        norm_q = math.sqrt(sum(a * a for a in query_embedding))
        if norm_q == 0:
            return []

        scored = []
        for r in rows:
            cid = r["chroma_id"]
            vec = id_vec_map.get(cid)
            if vec is None:
                continue
            dot = sum(a * b for a, b in zip(query_embedding, vec))
            norm_e = math.sqrt(sum(b * b for b in vec))
            sim = dot / (norm_q * norm_e) if norm_e else 0.0
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
        results = []
        for s in scored[:limit]:
            d = s[1]
            d["_score"] = round(s[0], 3)
            results.append(d)
        return results

    def list_recent(self, limit: int = 10) -> list[dict]:
        rows = self._conn.execute(
            "SELECT id, category, content, importance, created_at "
            "FROM long_term_memories ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            {
                "id": r["id"],
                "category": r["category"],
                "content": r["content"],
                "importance": r["importance"],
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    def delete(self, memory_id: int):
        with self._lock:
            row = self._conn.execute("SELECT chroma_id FROM long_term_memories WHERE id = ?", (memory_id,)).fetchone()
            if row and row["chroma_id"]:
                try:
                    self._ensure_chroma()
                    self._chroma_col.delete(ids=[row["chroma_id"]])
                except Exception as e:
                    logger.warning("ChromaDB delete failed for memory %s: %s", memory_id, e)
            self._conn.execute("DELETE FROM long_term_memories WHERE id = ?", (memory_id,))
            self._conn.commit()

    def clear_all(self):
        """Delete all LTM entries from both SQLite and ChromaDB."""
        with self._lock:
            rows = self._conn.execute("SELECT chroma_id FROM long_term_memories WHERE chroma_id IS NOT NULL").fetchall()
            chroma_ids = [r["chroma_id"] for r in rows]
            if chroma_ids:
                try:
                    self._ensure_chroma()
                    self._chroma_col.delete(ids=chroma_ids)
                except Exception as e:
                    logger.warning("ChromaDB batch delete failed in clear_all: %s", e)
            self._conn.execute("DELETE FROM long_term_memories")
            self._conn.commit()
            self._write_counter = 0
            logger.info("Cleared all long-term memories")
