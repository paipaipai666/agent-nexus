"""会话时间线存储 — 追加式事件日志 + 每步上下文快照（SQLite）。

事件在 ChatService._put_event 唯一收口处落盘；上下文快照由 agent
在每次模型调用前上报。所有写入包在 try/except 里 —— 观测路径永远不
阻塞 agent loop（写入失败只丢观测数据，不影响会话）。
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS session_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    run_id     TEXT NOT NULL,
    seq        INTEGER NOT NULL,
    ts         REAL NOT NULL,
    event_type TEXT NOT NULL,
    step_id    INTEGER NOT NULL DEFAULT 0,
    payload    TEXT NOT NULL DEFAULT '{}',
    UNIQUE (session_id, run_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_session_events_session ON session_events(session_id, id);

CREATE TABLE IF NOT EXISTS context_snapshots (
    session_id    TEXT NOT NULL,
    run_id        TEXT NOT NULL,
    step_id       INTEGER NOT NULL,
    ts            REAL NOT NULL,
    message_count INTEGER NOT NULL,
    char_count    INTEGER NOT NULL,
    messages      TEXT NOT NULL,
    PRIMARY KEY (session_id, run_id, step_id)
);
"""

# 流式 token 量太大且对时间线无意义 —— 逐 token 事件不落盘。
_SKIP_TYPES = frozenset({"stream_token", "stream_reasoning"})


class TimelineStore:
    """Append-only session event log + per-step context snapshots."""

    def __init__(self, db_path: str | None = None) -> None:
        if db_path is None:
            from agentnexus.core.config import get_settings
            db_path = get_settings().memory_db_path
        self._db_path = db_path
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None
        if db_path:
            try:
                Path(db_path).parent.mkdir(parents=True, exist_ok=True)
                self._conn = sqlite3.connect(db_path, check_same_thread=False)
                self._conn.execute("PRAGMA journal_mode=WAL")
                self._conn.execute("PRAGMA busy_timeout=5000")
                self._conn.row_factory = sqlite3.Row
                self._conn.executescript(_SCHEMA)
            except Exception as e:
                logger.warning("TimelineStore init failed (observability disabled): %s", e)
                self._conn = None

    # ── writes (never raise) ─────────────────────────────────────

    def record_event(
        self,
        session_id: str | None,
        run_id: str,
        seq: int,
        event_type: str,
        payload: dict[str, Any],
        step_id: int = 0,
    ) -> None:
        if self._conn is None or not session_id or event_type in _SKIP_TYPES:
            return
        try:
            with self._lock:
                self._conn.execute(
                    "INSERT OR IGNORE INTO session_events "
                    "(session_id, run_id, seq, ts, event_type, step_id, payload) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (session_id, run_id, seq, time.time(), event_type, step_id,
                     json.dumps(payload, ensure_ascii=False, default=str)),
                )
                self._conn.commit()
        except Exception as e:
            logger.debug("Timeline event persist failed: %s", e)

    def record_snapshot(
        self,
        session_id: str,
        run_id: str,
        step_id: int,
        messages: list[dict[str, Any]],
    ) -> None:
        if self._conn is None:
            return
        try:
            with self._lock:
                self._conn.execute(
                    "INSERT OR REPLACE INTO context_snapshots "
                    "(session_id, run_id, step_id, ts, message_count, char_count, messages) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (session_id, run_id, step_id, time.time(), len(messages),
                     sum(len(str(m.get("content", ""))) for m in messages),
                     json.dumps(messages, ensure_ascii=False, default=str)),
                )
                self._conn.commit()
        except Exception as e:
            logger.debug("Context snapshot persist failed: %s", e)

    # ── reads ────────────────────────────────────────────────────

    def list_events(
        self, session_id: str, after_id: int = 0, limit: int = 2000
    ) -> list[dict[str, Any]]:
        if self._conn is None:
            return []
        try:
            with self._lock:
                rows = self._conn.execute(
                    "SELECT id, run_id, seq, ts, event_type, step_id, payload "
                    "FROM session_events WHERE session_id = ? AND id > ? "
                    "ORDER BY id LIMIT ?",
                    (session_id, after_id, limit),
                ).fetchall()
            return [
                {
                    "id": r["id"], "run_id": r["run_id"], "seq": r["seq"],
                    "ts": r["ts"], "event_type": r["event_type"],
                    "step_id": r["step_id"],
                    "payload": json.loads(r["payload"]),
                }
                for r in rows
            ]
        except Exception as e:
            logger.debug("Timeline event read failed: %s", e)
            return []

    def get_snapshot(
        self, session_id: str, run_id: str, step_id: int
    ) -> dict[str, Any] | None:
        if self._conn is None:
            return None
        try:
            with self._lock:
                row = self._conn.execute(
                    "SELECT ts, message_count, char_count, messages FROM context_snapshots "
                    "WHERE session_id = ? AND run_id = ? AND step_id = ?",
                    (session_id, run_id, step_id),
                ).fetchone()
            if row is None:
                return None
            return {
                "ts": row["ts"], "message_count": row["message_count"],
                "char_count": row["char_count"],
                "messages": json.loads(row["messages"]),
            }
        except Exception as e:
            logger.debug("Context snapshot read failed: %s", e)
            return None

    def list_snapshot_steps(self, session_id: str, run_id: str) -> list[int]:
        if self._conn is None:
            return []
        try:
            with self._lock:
                rows = self._conn.execute(
                    "SELECT step_id FROM context_snapshots "
                    "WHERE session_id = ? AND run_id = ? ORDER BY step_id",
                    (session_id, run_id),
                ).fetchall()
            return [r["step_id"] for r in rows]
        except Exception as e:
            logger.debug("Snapshot step list failed: %s", e)
            return []


_instances: dict[str, TimelineStore] = {}


def get_timeline_store(db_path: str | None = None) -> TimelineStore:
    """Get or create the singleton store (keyed by db_path)."""
    if db_path is None:
        from agentnexus.core.config import get_settings
        db_path = get_settings().memory_db_path
    if db_path not in _instances:
        _instances[db_path] = TimelineStore(db_path)
    return _instances[db_path]


def _reset_timeline_store() -> None:
    """Test helper: drop all singletons."""
    _instances.clear()
