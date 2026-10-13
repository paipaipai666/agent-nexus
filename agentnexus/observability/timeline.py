"""会话时间线存储 — 追加式事件日志 + 每步上下文快照（SQLite）。

事件在 ChatService._put_event 落盘；上下文快照由 agent 在模型调用前上报。
事件与快照写入失败可丢弃观测数据；发送回执与任务恢复记录必须持久化，
失败时向调用方报告存储错误。
"""

from __future__ import annotations

import hashlib
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


_SKIP_TYPES = frozenset({"stream_token", "stream_reasoning"})


def _subagent_timeline_event(row: dict[str, Any]) -> dict[str, Any]:
    envelope = row["payload"]
    kind = envelope.get("kind", "")
    event_type = envelope.get("event_type")
    payload = envelope.get("event_payload")
    if not event_type or not isinstance(payload, dict):
        match kind:
            case "started":
                event_type, payload = "run_started", envelope
            case "finished":
                event_type, payload = "run_finished", envelope
            case "thinking":
                event_type, payload = "turn_journal", {
                    "event": "TOOLS_REQUESTED", "thought": envelope.get("content", ""),
                }
            case "tool_call" | "tool_result":
                event_type = "tool_start" if kind == "tool_call" else "tool_done"
                payload = {
                    **envelope, "name": envelope.get("tool_name", ""),
                    "id": envelope.get("tool_call_id", ""),
                }
            case "retry":
                event_type, payload = "subagent_retry", envelope
            case _:
                event_type, payload = f"subagent_{kind or 'unknown'}", envelope
    return {
        **row,
        "run_id": envelope.get("child_run_id") or f"{row['run_id']}:{envelope['subagent_id']}",
        "step_id": envelope.get("step_id", row["step_id"]),
        "event_type": event_type,
        "payload": payload,
    }


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
                logger.warning("TimelineStore init failed (storage unavailable): %s", e)
                if self._conn is not None:
                    self._conn.close()
                self._conn = None

    @staticmethod
    def submission_run_id(session_id: str, client_message_id: str) -> str:
        """Assign the same task identity to every retry of one submission."""
        digest = hashlib.sha256(json.dumps([session_id, client_message_id]).encode()).hexdigest()
        return f"run_{digest[:24]}"

    def claim_submission(self, session_id: str, client_message_id: str, content: str, attachments: list | None) -> tuple[str, bool]:
        """Durably reserve a submission before any agent or tool executes."""
        if self._conn is None:
            raise OSError("无法记录发送回执，请检查存储空间后重试")
        run_id = self.submission_run_id(session_id, client_message_id)
        payload = {"client_message_id": client_message_id, "question": content, "attachments": attachments or []}
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "INSERT OR IGNORE INTO session_events (session_id, run_id, seq, ts, event_type, payload) VALUES (?, ?, -1, ?, 'submission_received', ?)",
                (session_id, run_id, time.time(), json.dumps(payload, ensure_ascii=False)),
            )
            created = cursor.rowcount == 1
            row = self._conn.execute("SELECT payload FROM session_events WHERE session_id = ? AND run_id = ? AND seq = -1", (session_id, run_id)).fetchone()
            if json.loads(row["payload"]) != payload:
                raise ValueError("提交 ID 已被其他内容使用，请恢复原提交或新建任务")
        return run_id, created

    def get_submission(self, session_id: str, client_message_id: str) -> dict | None:
        """Look up the durable receipt without executing anything."""
        if self._conn is None:
            raise OSError("发送回执暂时不可用，请检查后端存储")
        run_id = self.submission_run_id(session_id, client_message_id)
        with self._lock:
            row = self._conn.execute("SELECT payload FROM session_events WHERE session_id = ? AND run_id = ? AND seq = -1", (session_id, run_id)).fetchone()
        return {**json.loads(row["payload"]), "run_id": run_id, "session_id": session_id} if row else None

    def save_run_record(self, record: dict) -> None:
        """Keep the latest recovery outcome in the existing event store."""
        if self._conn is None:
            raise OSError("无法保存任务恢复状态")
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO session_events (session_id, run_id, seq, ts, event_type, payload) VALUES (?, ?, -2, ?, 'run_recovery', ?) "
                "ON CONFLICT(session_id, run_id, seq) DO UPDATE SET ts = excluded.ts, payload = excluded.payload",
                (record["session_id"], record["run_id"], time.time(), json.dumps(record, ensure_ascii=False)),
            )

    def get_run_record(self, run_id: str) -> dict | None:
        """Read the outcome after a backend restart."""
        if self._conn is None:
            raise OSError("任务恢复状态暂时不可用")
        with self._lock:
            row = self._conn.execute("SELECT payload FROM session_events WHERE run_id = ? AND event_type = 'run_recovery' ORDER BY id DESC LIMIT 1", (run_id,)).fetchone()
        return json.loads(row["payload"]) if row else None

    def latest_run_id(self, session_id: str) -> str | None:
        """Find the most recently submitted or recorded task in a session."""
        if self._conn is None:
            raise OSError("任务恢复状态暂时不可用")
        with self._lock:
            row = self._conn.execute("SELECT run_id FROM session_events WHERE session_id = ? ORDER BY id DESC LIMIT 1", (session_id,)).fetchone()
        return row["run_id"] if row else None

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

    def list_events(
        self, session_id: str, after_id: int = 0, limit: int = 2000,
        *, agent_id: str | None = None, normalize: bool = True,
    ) -> list[dict[str, Any]]:
        if self._conn is None:
            return []
        try:
            scope = " AND event_type NOT IN ('submission_received', 'run_recovery')"
            params: list[Any] = [session_id, after_id]
            if agent_id == "main":
                scope += " AND event_type != 'subagent_event'"
            elif agent_id is not None:
                scope += " AND event_type = 'subagent_event' AND json_extract(payload, '$.subagent_id') = ?"
                params.append(agent_id)
            params.append(limit)
            with self._lock:
                rows = self._conn.execute(
                    "SELECT id, run_id, seq, ts, event_type, step_id, payload "
                    "FROM session_events WHERE session_id = ? AND id > ?"
                    + scope + " ORDER BY id LIMIT ?",
                    params,
                ).fetchall()
            events = [
                {
                    "id": r["id"], "run_id": r["run_id"], "seq": r["seq"],
                    "ts": r["ts"], "event_type": r["event_type"],
                    "step_id": r["step_id"],
                    "payload": json.loads(r["payload"]),
                }
                for r in rows
            ]
            if agent_id is not None and agent_id != "main" and normalize:
                return [_subagent_timeline_event(event) for event in events]
            return events
        except Exception as e:
            logger.debug("Timeline event read failed: %s", e)
            return []

    def get_snapshot(
        self, session_id: str, run_id: str, step_id: int,
        *, agent_id: str | None = None,
    ) -> dict[str, Any] | None:
        if self._conn is None:
            return None
        try:
            with self._lock:
                if agent_id is not None:
                    if agent_id == "main":
                        owned = self._conn.execute(
                            "SELECT 1 FROM session_events WHERE session_id = ? AND run_id = ? "
                            "AND event_type != 'subagent_event' LIMIT 1",
                            (session_id, run_id),
                        ).fetchone()
                    else:
                        owned = self._conn.execute(
                            "SELECT 1 FROM session_events WHERE session_id = ? "
                            "AND event_type = 'subagent_event' "
                            "AND json_extract(payload, '$.subagent_id') = ? "
                            "AND json_extract(payload, '$.child_run_id') = ? LIMIT 1",
                            (session_id, agent_id, run_id),
                        ).fetchone()
                    if owned is None:
                        return None
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
_instances_lock = threading.Lock()


def get_timeline_store(db_path: str | None = None) -> TimelineStore:
    """Get or create the singleton store (keyed by db_path)."""
    if db_path is None:
        from agentnexus.core.config import get_settings
        db_path = get_settings().memory_db_path
    with _instances_lock:
        if db_path in _instances:
            return _instances[db_path]
        store = TimelineStore(db_path)
        if store._conn is not None:
            _instances[db_path] = store
        return store


def _reset_timeline_store() -> None:
    """Test helper: drop all singletons."""
    with _instances_lock:
        _instances.clear()
