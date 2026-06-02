"""Session-scoped todo list for agent task tracking.

Supports optional SQLite persistence for crash recovery.
When db_path is provided, all mutations are persisted and
the list is restored on construction.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS session_todos (
    id         INTEGER NOT NULL,
    session_id TEXT    NOT NULL,
    description TEXT   NOT NULL,
    status     TEXT    NOT NULL DEFAULT 'pending',
    created_at TEXT    NOT NULL,
    updated_at TEXT    NOT NULL,
    PRIMARY KEY (session_id, id)
);
"""


@dataclass
class TodoItem:
    id: int
    description: str
    status: str  # "pending" | "in_progress" | "done"
    created_at: str
    updated_at: str


class SessionTodoList:
    """Todo list with optional SQLite persistence.

    When *db_path* is given, every add/update is written to the database
    and the list is restored from it on construction.  Otherwise the list
    is purely in-memory (original behaviour).
    """

    VALID_STATUSES = ("pending", "in_progress", "done")

    def __init__(self, session_id: str = "", db_path: str | None = None) -> None:
        self._session_id = session_id
        self._items: list[TodoItem] = []
        self._next_id: int = 1
        self._db: sqlite3.Connection | None = None

        if db_path and session_id:
            try:
                self._db = sqlite3.connect(db_path, check_same_thread=False)
                self._db.execute("PRAGMA journal_mode=WAL")
                self._db.execute(_SCHEMA)
                self._db.commit()
                self._load()
            except Exception as e:
                logger.warning("TodoList SQLite init failed, falling back to in-memory: %s", e)
                self._db = None

    # ── persistence helpers ──────────────────────────────────────

    def _load(self):
        """Load items from SQLite into memory."""
        if not self._db:
            return
        try:
            rows = self._db.execute(
                "SELECT id, description, status, created_at, updated_at "
                "FROM session_todos WHERE session_id = ? ORDER BY id",
                (self._session_id,),
            ).fetchall()
            for row in rows:
                self._items.append(TodoItem(
                    id=row[0], description=row[1], status=row[2],
                    created_at=row[3], updated_at=row[4],
                ))
            if self._items:
                self._next_id = max(i.id for i in self._items) + 1
        except Exception as e:
            logger.warning("TodoList load failed: %s", e)

    def _persist_item(self, item: TodoItem):
        """Upsert a single item to SQLite."""
        if not self._db:
            return
        try:
            self._db.execute(
                "INSERT OR REPLACE INTO session_todos "
                "(id, session_id, description, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (item.id, self._session_id, item.description,
                 item.status, item.created_at, item.updated_at),
            )
            self._db.commit()
        except Exception as e:
            logger.warning("TodoList persist failed: %s", e)

    # ── public API ───────────────────────────────────────────────

    def add(self, description: str) -> TodoItem:
        now = datetime.now(timezone.utc).isoformat()
        item = TodoItem(
            id=self._next_id,
            description=description,
            status="pending",
            created_at=now,
            updated_at=now,
        )
        self._items.append(item)
        self._next_id += 1
        self._persist_item(item)
        return item

    def update(self, item_id: int, status: str) -> TodoItem:
        if status not in self.VALID_STATUSES:
            raise ValueError(f"Invalid status: {status!r}. Must be one of {self.VALID_STATUSES}")
        for item in self._items:
            if item.id == item_id:
                item.status = status
                item.updated_at = datetime.now(timezone.utc).isoformat()
                self._persist_item(item)
                return item
        raise KeyError(f"Todo item {item_id} not found")

    def list_items(self) -> list[TodoItem]:
        return list(self._items)

    def format_context(self) -> str:
        """Return formatted todo context for prompt injection. Empty if no active items."""
        active = [i for i in self._items if i.status != "done"]
        if not active:
            return ""
        lines = ["== 当前任务清单 =="]
        for item in self._items:
            marker = {"done": "[✓]", "in_progress": "[→]", "pending": "[·]"}.get(item.status, "[·]")
            lines.append(f"- {marker} {item.description}")
        return "\n".join(lines) + "\n\n"
