"""Tests for SessionTodoList."""

import pytest

from agentnexus.memory.todo import SessionTodoList, TodoItem


class TestTodoItem:
    def test_fields(self):
        item = TodoItem(id=1, description="test", status="pending",
                        created_at="2026-01-01T00:00:00", updated_at="2026-01-01T00:00:00")
        assert item.id == 1
        assert item.description == "test"
        assert item.status == "pending"


class TestSessionTodoList:
    def test_add(self):
        tl = SessionTodoList()
        item = tl.add("build auth module")
        assert item.id == 1
        assert item.description == "build auth module"
        assert item.status == "pending"
        assert len(tl.list_items()) == 1

    def test_add_increments_id(self):
        tl = SessionTodoList()
        a = tl.add("task a")
        b = tl.add("task b")
        assert a.id == 1
        assert b.id == 2

    def test_update_status(self):
        tl = SessionTodoList()
        tl.add("task a")
        updated = tl.update(1, "in_progress")
        assert updated.status == "in_progress"

    def test_update_to_done(self):
        tl = SessionTodoList()
        tl.add("task a")
        tl.update(1, "done")
        assert tl.list_items()[0].status == "done"

    def test_update_nonexistent_raises(self):
        tl = SessionTodoList()
        with pytest.raises(KeyError):
            tl.update(999, "done")

    def test_update_invalid_status_raises(self):
        tl = SessionTodoList()
        tl.add("task a")
        with pytest.raises(ValueError):
            tl.update(1, "invalid")

    def test_list_items_returns_copy(self):
        tl = SessionTodoList()
        tl.add("task a")
        items = tl.list_items()
        items.clear()
        assert len(tl.list_items()) == 1

    def test_format_context_empty(self):
        tl = SessionTodoList()
        assert tl.format_context() == ""

    def test_format_context_with_items(self):
        tl = SessionTodoList()
        tl.add("task a")
        tl.add("task b")
        tl.update(1, "in_progress")
        ctx = tl.format_context()
        assert "task a" in ctx
        assert "task b" in ctx
        assert "→" in ctx

    def test_format_context_all_done_returns_empty(self):
        tl = SessionTodoList()
        tl.add("task a")
        tl.update(1, "done")
        assert tl.format_context() == ""
