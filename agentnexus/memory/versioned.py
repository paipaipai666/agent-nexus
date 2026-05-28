"""Conversation version control — linear checkpoint system for chat sessions.

Each user question → agent answer turn creates a checkpoint. Checkpoints form a
linear chain (parent → child). STM is stored as full JSON snapshots.
"""

import logging
import sqlite3
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversation_checkpoints (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    parent_id TEXT,
    stm_snapshot TEXT NOT NULL,
    question TEXT,
    answer TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS conversation_sessions (
    session_id TEXT PRIMARY KEY,
    workspace_path TEXT NOT NULL,
    profile TEXT,
    head_checkpoint_id TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_cp_session ON conversation_checkpoints(session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_sessions_workspace ON conversation_sessions(workspace_path, updated_at);
"""

# Migration: add head_checkpoint_id column if missing
_MIGRATION_SQL = """
ALTER TABLE conversation_sessions ADD COLUMN head_checkpoint_id TEXT;
"""


class ConversationVersionManager:
    """Linear checkpoint manager for chat conversations.

    Usage:
        mgr = ConversationVersionManager(session_id, db_path)
        # ... agent answers ...
        mgr.commit(stm, question, answer)
        mgr.undo()      # back to parent checkpoint
        mgr.redo()      # forward again
        mgr.log()       # list checkpoints
    """

    def __init__(
        self,
        session_id: str,
        db_path: str,
        workspace_path: str | None = None,
        profile: str | None = None,
    ):
        self.session_id = session_id
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._migrate()
        self._conn.commit()
        self._redo_stack: list[str] = []
        self._workspace_path = self.normalize_workspace_path(workspace_path) if workspace_path else ""
        self._profile = profile or ""
        if self._workspace_path:
            self.register_session(self._workspace_path, self._profile)

    # ── public API ─────────────────────────────────────────────────

    def commit(self, stm_snapshot: str, question: str = "", answer: str = "") -> str:
        """Create a checkpoint. Returns the new checkpoint ID."""
        from agentnexus.core.hooks import HookType, get_hook_manager

        hook_mgr = get_hook_manager()
        hook_mgr.fire(HookType.BEFORE_CHECKPOINT, {
            "session_id": self.session_id, "question": question,
        })

        cp_id = uuid.uuid4().hex[:8]
        parent_id = self._current_head_id()

        self._conn.execute(
            "INSERT INTO conversation_checkpoints (id, session_id, parent_id, "
            "stm_snapshot, question, answer) VALUES (?, ?, ?, ?, ?, ?)",
            (cp_id, self.session_id, parent_id, stm_snapshot, question, answer),
        )

        self._set_head(cp_id)
        self._touch_session()
        self._conn.commit()

        # New commit after undo → clear redo stack
        self._redo_stack.clear()

        hook_mgr.fire(HookType.AFTER_CHECKPOINT, {
            "session_id": self.session_id, "cp_id": cp_id,
            "parent_id": parent_id, "question": question,
        })
        return cp_id

    def register_session(self, workspace_path: str, profile: str = "") -> None:
        """Record the workspace that owns this conversation session."""
        normalized = self.normalize_workspace_path(workspace_path)
        self._workspace_path = normalized
        self._profile = profile or self._profile
        self._conn.execute(
            "INSERT INTO conversation_sessions (session_id, workspace_path, profile) "
            "VALUES (?, ?, ?) "
            "ON CONFLICT(session_id) DO UPDATE SET "
            "workspace_path = excluded.workspace_path, "
            "profile = COALESCE(NULLIF(excluded.profile, ''), conversation_sessions.profile), "
            "updated_at = datetime('now')",
            (self.session_id, normalized, self._profile),
        )
        self._conn.commit()

    @staticmethod
    def normalize_workspace_path(workspace_path: str | None = None) -> str:
        """Normalize workspace paths so session ownership checks are stable."""
        path = Path(workspace_path or Path.cwd()).expanduser().resolve()
        normalized = str(path)
        return normalized.casefold() if Path(normalized).drive else normalized

    @classmethod
    def find_latest_session(cls, db_path: str, workspace_path: str) -> str | None:
        """Return the most recently updated session for a workspace, if any."""
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            try:
                conn.executescript(SCHEMA)
            except sqlite3.OperationalError:
                return None
            normalized = cls.normalize_workspace_path(workspace_path)
            row = conn.execute(
                "SELECT s.session_id FROM conversation_sessions s "
                "WHERE s.workspace_path = ? "
                "AND EXISTS ("
                "  SELECT 1 FROM conversation_checkpoints c "
                "  WHERE c.session_id = s.session_id"
                ") "
                "ORDER BY s.updated_at DESC, s.created_at DESC, s.rowid DESC "
                "LIMIT 1",
                (normalized,),
            ).fetchone()
            return row["session_id"] if row else None
        finally:
            conn.close()

    @classmethod
    def session_belongs_to_workspace(cls, db_path: str, session_id: str, workspace_path: str) -> bool:
        """Return True only when a session is registered for the given workspace."""
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            try:
                conn.executescript(SCHEMA)
            except sqlite3.OperationalError:
                return False
            normalized = cls.normalize_workspace_path(workspace_path)
            row = conn.execute(
                "SELECT 1 FROM conversation_sessions "
                "WHERE session_id = ? AND workspace_path = ?",
                (session_id, normalized),
            ).fetchone()
            return row is not None
        finally:
            conn.close()

    def undo(self) -> dict | None:
        """Move HEAD to parent checkpoint. Returns the new current checkpoint, or None."""
        current = self._current_checkpoint()
        if current is None or current["parent_id"] is None:
            return None

        parent = self._get_checkpoint(current["parent_id"])
        if parent is None:
            return None

        # Move head to parent
        self._set_head(parent["id"])
        self._conn.commit()

        # Push current onto redo stack
        self._redo_stack.append(current["id"])

        return dict(parent)

    def redo(self) -> dict | None:
        """Re-apply a previously undone checkpoint."""
        if not self._redo_stack:
            return None

        cp_id = self._redo_stack.pop()
        cp = self._get_checkpoint(cp_id)
        if cp is None:
            return None

        self._set_head(cp_id)
        self._conn.commit()

        return dict(cp)

    def log(self) -> list[dict]:
        """List checkpoints for current session, newest first."""
        head_id = self._current_head_id()
        rows = self._ancestor_chain(head_id)

        result = []
        for r in rows:
            d = dict(r)
            d["is_head"] = (r["id"] == head_id)
            result.append(d)
        return result

    def status(self) -> dict:
        """Return current HEAD and undo/redo availability."""
        head = self._current_checkpoint()
        return {
            "session_id": self.session_id,
            "head": head if head else None,
            "can_undo": head is not None and head.get("parent_id") is not None,
            "can_redo": len(self._redo_stack) > 0,
        }

    def get_head_stm(self) -> str:
        """Return the STM snapshot JSON for the current HEAD checkpoint."""
        head = self._current_checkpoint()
        if head is None:
            return ""
        return head["stm_snapshot"]

    def reset(self):
        """Delete all checkpoints for this session."""
        self._conn.execute(
            "DELETE FROM conversation_checkpoints WHERE session_id = ?",
            (self.session_id,),
        )
        self._conn.commit()
        self._redo_stack.clear()

    # ── internal helpers ──────────────────────────────────────────

    def _migrate(self):
        """Apply schema migrations for existing databases."""
        try:
            self._conn.execute(_MIGRATION_SQL)
        except sqlite3.OperationalError:
            pass  # Column already exists

    def _current_head_id(self) -> str | None:
        """Get the current HEAD checkpoint ID from conversation_sessions."""
        row = self._conn.execute(
            "SELECT head_checkpoint_id FROM conversation_sessions WHERE session_id = ?",
            (self.session_id,),
        ).fetchone()
        if row is None:
            return None
        return row["head_checkpoint_id"]

    def _set_head(self, cp_id: str):
        """Update the HEAD checkpoint ID in conversation_sessions."""
        # Ensure session exists first
        self._conn.execute(
            "INSERT OR IGNORE INTO conversation_sessions (session_id, workspace_path, profile) "
            "VALUES (?, ?, ?)",
            (self.session_id, self._workspace_path or "", self._profile),
        )
        self._conn.execute(
            "UPDATE conversation_sessions SET head_checkpoint_id = ?, updated_at = datetime('now') "
            "WHERE session_id = ?",
            (cp_id, self.session_id),
        )

    def _current_checkpoint(self) -> dict | None:
        head_id = self._current_head_id()
        if head_id is None:
            return None
        return self._get_checkpoint(head_id)

    def _get_checkpoint(self, cp_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM conversation_checkpoints WHERE id = ?", (cp_id,)
        ).fetchone()
        return dict(row) if row else None

    def _touch_session(self):
        if not self._workspace_path:
            return
        self._conn.execute(
            "UPDATE conversation_sessions SET updated_at = datetime('now') WHERE session_id = ?",
            (self.session_id,),
        )

    def _ancestor_chain(self, head_id: str | None) -> list[sqlite3.Row]:
        """Walk the parent chain from head_id back to root."""
        rows = []
        seen: set[str] = set()
        current = head_id
        while current and current not in seen:
            row = self._conn.execute(
                "SELECT * FROM conversation_checkpoints WHERE id = ?", (current,)
            ).fetchone()
            if row is None:
                break
            seen.add(current)
            rows.append(row)
            current = row["parent_id"]
        return rows
