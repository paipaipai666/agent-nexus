"""Conversation version control — git-like checkpoint system for chat sessions.

Each user question → agent answer turn creates a checkpoint. Checkpoints form a DAG
(supports branching). STM is stored as full JSON snapshots; LTM changes are tracked
via incremental references for efficient rollback.
"""

import json
import sqlite3
import uuid
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversation_checkpoints (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    branch_name TEXT NOT NULL DEFAULT 'main',
    parent_id TEXT,
    stm_snapshot TEXT NOT NULL,
    question TEXT,
    answer TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS checkpoint_ltm_refs (
    checkpoint_id TEXT NOT NULL,
    ltm_memory_id INTEGER NOT NULL,
    FOREIGN KEY (checkpoint_id) REFERENCES conversation_checkpoints(id)
);

CREATE TABLE IF NOT EXISTS conversation_branches (
    session_id TEXT NOT NULL,
    branch_name TEXT NOT NULL,
    head_checkpoint_id TEXT NOT NULL,
    PRIMARY KEY (session_id, branch_name)
);

CREATE INDEX IF NOT EXISTS idx_cp_session ON conversation_checkpoints(session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_cp_ltm ON checkpoint_ltm_refs(checkpoint_id);
"""


class ConversationVersionManager:
    """Git-like checkpoint manager for chat conversations.

    Usage:
        mgr = ConversationVersionManager(session_id, db_path)
        # ... agent answers ...
        mgr.commit(stm, question, answer, new_ltm_ids=[42, 43])
        mgr.undo()      # back to parent checkpoint
        mgr.redo()      # forward again
        mgr.log()       # list checkpoints
        mgr.branch("experiment")  # fork from current position
    """

    def __init__(self, session_id: str, db_path: str):
        self.session_id = session_id
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()
        self._redo_stack: list[str] = []
        self._current_branch_name: str = ""  # lazy-init from DB

    # ── public API ─────────────────────────────────────────────────

    def commit(self, stm_snapshot: str, question: str = "", answer: str = "",
               new_ltm_ids: list[int] | None = None) -> str:
        """Create a checkpoint. Returns the new checkpoint ID."""
        cp_id = uuid.uuid4().hex[:8]
        parent_id = self._current_head_id()
        branch = self._current_branch()

        self._conn.execute(
            "INSERT INTO conversation_checkpoints (id, session_id, branch_name, "
            "parent_id, stm_snapshot, question, answer) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (cp_id, self.session_id, branch, parent_id, stm_snapshot, question, answer),
        )

        for ltm_id in (new_ltm_ids or []):
            self._conn.execute(
                "INSERT INTO checkpoint_ltm_refs (checkpoint_id, ltm_memory_id) VALUES (?, ?)",
                (cp_id, ltm_id),
            )

        self._upsert_branch_head(branch, cp_id)
        self._conn.commit()

        # New commit after undo → clear redo stack (like git)
        self._redo_stack.clear()

        return cp_id

    def undo(self) -> dict | None:
        """Move HEAD to parent checkpoint. Returns the new current checkpoint, or None."""
        current = self._current_checkpoint()
        if current is None or current["parent_id"] is None:
            return None

        parent = self._get_checkpoint(current["parent_id"])
        if parent is None:
            return None

        # Roll back LTM entries from current checkpoint
        self._delete_ltm_refs(current["id"])

        # Move head to parent
        branch = self._current_branch()
        self._upsert_branch_head(branch, parent["id"])
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

        branch = self._current_branch()
        self._upsert_branch_head(branch, cp_id)
        self._conn.commit()

        return dict(cp)

    def log(self, all_branches: bool = False) -> list[dict]:
        """List checkpoints for current session, newest first."""
        if all_branches:
            rows = self._conn.execute(
                "SELECT * FROM conversation_checkpoints WHERE session_id = ? "
                "ORDER BY created_at DESC",
                (self.session_id,),
            ).fetchall()
        else:
            branch = self._current_branch()
            # Walk the DAG from head following parent chain
            head_id = self._head_id_for_branch(branch)
            rows = self._ancestor_chain(head_id)

        head_id = self._current_head_id()
        result = []
        for r in rows:
            d = dict(r)
            d["is_head"] = (r["id"] == head_id)
            result.append(d)
        return result

    def branch(self, name: str) -> str:
        """Create a new branch from current HEAD and switch to it."""
        head_id = self._current_head_id()
        if head_id:
            self._upsert_branch_head(name, head_id)
            self._conn.commit()
        self._current_branch_name = name
        return name

    def checkout(self, ref: str) -> dict | None:
        """Switch to a checkpoint by ID or branch name. Returns the checkpoint."""
        # Try as checkpoint ID first
        cp = self._get_checkpoint(ref)
        switched_branch = None
        if cp is None:
            # Try as branch name
            head_id = self._head_id_for_branch(ref)
            if head_id:
                cp = self._get_checkpoint(head_id)
                switched_branch = ref
        if cp is None:
            return None

        # Clean up LTM from checkpoints on the old path but not the new path
        old_head_id = self._current_head_id()
        if old_head_id and old_head_id != cp["id"]:
            old_chain = self._ancestor_chain_ids(old_head_id)
            new_chain = self._ancestor_chain_ids(cp["id"])
            for skipped_id in old_chain - new_chain:
                self._delete_ltm_refs(skipped_id)

        # If switching to a different branch, use its branch name for heads
        target_branch = switched_branch or self._current_branch()
        self._upsert_branch_head(target_branch, cp["id"])
        if switched_branch:
            self._current_branch_name = switched_branch
        self._conn.commit()
        self._redo_stack.clear()

        return dict(cp)

    def diff(self, ref1: str = "", ref2: str = "") -> dict:
        """Compare two checkpoints. Defaults: ref1=parent, ref2=current HEAD."""
        cp2 = self._current_checkpoint() if not ref2 else self._get_checkpoint(ref2)
        cp1 = self._get_checkpoint(ref1) if ref1 else (
            self._get_checkpoint(cp2["parent_id"]) if cp2 and cp2.get("parent_id") else None
        )

        if cp1 is None or cp2 is None:
            return {"error": "Need two valid checkpoints to diff"}

        stm1 = json.loads(cp1["stm_snapshot"])
        stm2 = json.loads(cp2["stm_snapshot"])

        msgs1 = stm1.get("messages", [])
        msgs2 = stm2.get("messages", [])

        # Compare by (role, content) — timestamps differ between snapshots
        def _key(m):
            return (m.get("role", ""), m.get("content", ""))
        keys1 = [_key(m) for m in msgs1]
        keys2 = [_key(m) for m in msgs2]

        added_msgs = [m for i, m in enumerate(msgs2) if _key(m) not in keys1]
        removed_msgs = [m for i, m in enumerate(msgs1) if _key(m) not in keys2]

        ltm1 = set(r["ltm_memory_id"] for r in self._conn.execute(
            "SELECT ltm_memory_id FROM checkpoint_ltm_refs WHERE checkpoint_id = ?", (cp1["id"],)
        ).fetchall())
        ltm2 = set(r["ltm_memory_id"] for r in self._conn.execute(
            "SELECT ltm_memory_id FROM checkpoint_ltm_refs WHERE checkpoint_id = ?", (cp2["id"],)
        ).fetchall())

        return {
            "ref1": cp1["id"],
            "ref2": cp2["id"],
            "stm_messages_added": len(added_msgs),
            "stm_messages_removed": len(removed_msgs),
            "stm_summary_changed": stm1.get("summary") != stm2.get("summary"),
            "ltm_added": list(ltm2 - ltm1),
            "ltm_removed": list(ltm1 - ltm2),
        }

    def status(self) -> dict:
        """Return current branch, HEAD, and redo availability."""
        head = self._current_checkpoint()
        return {
            "session_id": self.session_id,
            "branch": self._current_branch(),
            "head": dict(head) if head else None,
            "can_undo": head is not None and head.get("parent_id") is not None,
            "can_redo": len(self._redo_stack) > 0,
        }

    def get_head_stm(self) -> str:
        """Return the STM snapshot JSON for the current HEAD checkpoint."""
        head = self._current_checkpoint()
        if head is None:
            return ""
        return head["stm_snapshot"]

    def get_head_ltm_ids(self) -> list[int]:
        """Return LTM IDs associated with the current HEAD checkpoint."""
        head = self._current_checkpoint()
        if head is None:
            return []
        rows = self._conn.execute(
            "SELECT ltm_memory_id FROM checkpoint_ltm_refs WHERE checkpoint_id = ?",
            (head["id"],),
        ).fetchall()
        return [r["ltm_memory_id"] for r in rows]

    def reset(self):
        """Delete all checkpoints and branch data for this session."""
        cp_ids = self._conn.execute(
            "SELECT id FROM conversation_checkpoints WHERE session_id = ?",
            (self.session_id,),
        ).fetchall()
        for r in cp_ids:
            self._conn.execute(
                "DELETE FROM checkpoint_ltm_refs WHERE checkpoint_id = ?", (r["id"],)
            )
        self._conn.execute(
            "DELETE FROM conversation_checkpoints WHERE session_id = ?",
            (self.session_id,),
        )
        self._conn.execute(
            "DELETE FROM conversation_branches WHERE session_id = ?",
            (self.session_id,),
        )
        self._conn.commit()
        self._redo_stack.clear()
        self._current_branch_name = ""

    # ── internal helpers ──────────────────────────────────────────

    def _current_branch(self) -> str:
        """Get or create the default branch name for this session."""
        if self._current_branch_name:
            return self._current_branch_name
        row = self._conn.execute(
            "SELECT branch_name FROM conversation_branches WHERE session_id = ? LIMIT 1",
            (self.session_id,),
        ).fetchone()
        self._current_branch_name = row["branch_name"] if row else "main"
        return self._current_branch_name

    def _current_head_id(self) -> str | None:
        branch = self._current_branch()
        return self._head_id_for_branch(branch)

    def _head_id_for_branch(self, branch: str) -> str | None:
        row = self._conn.execute(
            "SELECT head_checkpoint_id FROM conversation_branches "
            "WHERE session_id = ? AND branch_name = ?",
            (self.session_id, branch),
        ).fetchone()
        return row["head_checkpoint_id"] if row else None

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

    def _upsert_branch_head(self, branch: str, cp_id: str):
        self._conn.execute(
            "INSERT OR REPLACE INTO conversation_branches (session_id, branch_name, head_checkpoint_id) "
            "VALUES (?, ?, ?)",
            (self.session_id, branch, cp_id),
        )

    def _delete_ltm_refs(self, cp_id: str):
        """Delete LTM entries referenced by a checkpoint."""
        ltm_ids = [r["ltm_memory_id"] for r in self._conn.execute(
            "SELECT ltm_memory_id FROM checkpoint_ltm_refs WHERE checkpoint_id = ?", (cp_id,)
        ).fetchall()]
        for ltm_id in ltm_ids:
            self._conn.execute("DELETE FROM long_term_memories WHERE id = ?", (ltm_id,))
        self._conn.execute("DELETE FROM checkpoint_ltm_refs WHERE checkpoint_id = ?", (cp_id,))

    def _ancestor_chain_ids(self, head_id: str | None) -> set[str]:
        """Return the set of checkpoint IDs on the ancestor chain from head_id to root."""
        ids: set[str] = set()
        current = head_id
        while current and current not in ids:
            ids.add(current)
            row = self._conn.execute(
                "SELECT parent_id FROM conversation_checkpoints WHERE id = ?", (current,)
            ).fetchone()
            current = row["parent_id"] if row else None
        return ids

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
