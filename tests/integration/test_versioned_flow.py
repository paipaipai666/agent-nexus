"""Integration tests: Conversation version manager flow — rollback and undo/redo.

These tests use ConversationVersionManager directly but simulate realistic agent
conversation flows with multiple turns.
"""

import pytest

from agentnexus.memory.short_term import ShortTermMemory
from agentnexus.memory.versioned import ConversationVersionManager


@pytest.fixture
def mgr(temp_agentnexus_home):
    from agentnexus.core.config import get_settings
    inst = ConversationVersionManager("test-session", get_settings().memory_db_path)
    yield inst
    inst._conn.close()


def _make_stm(messages: list[dict] | None = None) -> str:
    stm = ShortTermMemory()
    for m in (messages or []):
        stm.append(m.get("role", "user"), m.get("content", ""))
    return stm.to_json()


class TestSessionRollbackFlow:
    """Integration tests for undo/redo in a simulated multi-turn conversation."""

    def test_multi_turn_undo_redo_cycle(self, mgr):
        """Full undo/redo cycle across 3 turns preserves linear history."""
        ids = []
        for i in range(1, 4):
            stm = _make_stm([{"role": "user", "content": f"q{i}"}])
            cp_id = mgr.commit(stm, question=f"q{i}", answer=f"a{i}")
            ids.append(cp_id)

        assert mgr.status()["head"]["id"] == ids[2]

        # Undo all the way back
        assert mgr.undo()["id"] == ids[1]
        assert mgr.undo()["id"] == ids[0]

        # Redo all the way forward
        assert mgr.redo()["id"] == ids[1]
        assert mgr.redo()["id"] == ids[2]

        assert mgr.status()["head"]["id"] == ids[2]
        assert mgr.status()["can_redo"] is False  # redo stack is empty after redoing all
        assert mgr.status()["can_undo"] is True

    def test_undo_preserves_stm_snapshot(self, mgr):
        """Undo restores the STM snapshot from the parent checkpoint."""
        stm1 = _make_stm([{"role": "user", "content": "q1"}])
        mgr.commit(stm1, question="q1", answer="a1")

        stm2 = _make_stm([{"role": "user", "content": "q1"},
                          {"role": "assistant", "content": "a1"},
                          {"role": "user", "content": "q2"}])
        mgr.commit(stm2, question="q2", answer="a2")

        mgr.undo()
        head_stm = mgr.get_head_stm()
        assert head_stm == stm1

    def test_new_commit_after_undo_discards_redo(self, mgr):
        """New commit after undo clears the redo stack."""
        mgr.commit(_make_stm([{"role": "user", "content": "q1"}]), question="q1", answer="a1")
        mgr.commit(_make_stm([{"role": "user", "content": "q2"}]), question="q2", answer="a2")
        mgr.undo()

        assert mgr.status()["can_redo"] is True

        # New commit after undo
        mgr.commit(_make_stm([{"role": "user", "content": "q3"}]), question="q3", answer="a3")
        assert mgr.status()["can_redo"] is False

    def test_log_after_undo_shows_correct_chain(self, mgr):
        """log() shows the correct ancestor chain after undo."""
        cp1 = mgr.commit(_make_stm([{"role": "user", "content": "q1"}]), question="q1", answer="a1")
        cp2 = mgr.commit(_make_stm([{"role": "user", "content": "q2"}]), question="q2", answer="a2")
        mgr.commit(_make_stm([{"role": "user", "content": "q3"}]), question="q3", answer="a3")

        mgr.undo()  # back to cp2
        entries = mgr.log()
        assert len(entries) == 2
        assert entries[0]["id"] == cp2
        assert entries[0]["is_head"] is True
        assert entries[1]["id"] == cp1

    def test_reset_clears_all_checkpoints(self, mgr):
        """reset() removes all checkpoints."""
        mgr.commit(_make_stm([{"role": "user", "content": "q1"}]), question="q1", answer="a1")
        mgr.commit(_make_stm([{"role": "user", "content": "q2"}]), question="q2", answer="a2")

        mgr.reset()
        assert mgr.status()["head"] is None
        assert mgr.log() == []
