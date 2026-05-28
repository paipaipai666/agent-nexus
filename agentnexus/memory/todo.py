"""Session-scoped todo list for agent task tracking."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class TodoItem:
    id: int
    description: str
    status: str  # "pending" | "in_progress" | "done"
    created_at: str
    updated_at: str


class SessionTodoList:
    """In-memory todo list with session lifecycle. Not persisted."""

    VALID_STATUSES = ("pending", "in_progress", "done")

    def __init__(self) -> None:
        self._items: list[TodoItem] = []
        self._next_id: int = 1

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
        return item

    def update(self, item_id: int, status: str) -> TodoItem:
        if status not in self.VALID_STATUSES:
            raise ValueError(f"Invalid status: {status!r}. Must be one of {self.VALID_STATUSES}")
        for item in self._items:
            if item.id == item_id:
                item.status = status
                item.updated_at = datetime.now(timezone.utc).isoformat()
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
