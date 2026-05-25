"""Integration tests: Conversation version manager flow — rollback, branching, LTM isolation.

These tests use ConversationVersionManager directly but simulate realistic agent
conversation flows with multiple turns and LTM interactions.
"""

import json

import pytest

from agentnexus.memory.short_term import ShortTermMemory
from agentnexus.memory.versioned import ConversationVersionManager


@pytest.fixture
def mgr(temp_agentnexus_home):
    from agentnexus.core.config import get_settings
    inst = ConversationVersionManager("test-session", get_settings().memory_db_path)
    yield inst
    inst._conn.close()


@pytest.fixture
def mgr_with_ltm(mgr):
    """Ensure long_term_memories table exists for LTM isolation tests."""
    mgr._conn.execute("""
        CREATE TABLE IF NOT EXISTS long_term_memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            category TEXT DEFAULT 'general',
            content TEXT NOT NULL,
            importance REAL DEFAULT 0.5,
            metadata_json TEXT DEFAULT '{}',
            chroma_id TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    return mgr


def _make_stm(messages: list[dict] | None = None) -> str:
    stm = ShortTermMemory()
    for m in (messages or []):
        stm.append(m.get("role", "user"), m.get("content", ""))
    return stm.to_json()


class TestSessionRollbackFlow:
    """Integration tests for undo/redo in a simulated multi-turn conversation."""

    def test_rollback_restores_stm_state(self, mgr):
        stm1 = ShortTermMemory()
        stm1.append("user", "hello")
        stm1.append("assistant", "hi there")
        cp1 = mgr.commit(stm1.to_json(), question="hello", answer="hi there")

        stm2 = ShortTermMemory()
        stm2.append("user", "hello")
        stm2.append("assistant", "hi there")
        stm2.append("user", "what is python")
        stm2.append("assistant", "a language")
        cp2 = mgr.commit(stm2.to_json(), question="what is python", answer="a language")

        stm3 = ShortTermMemory()
        stm3.append("user", "hello")
        stm3.append("assistant", "hi there")
        stm3.append("user", "what is python")
        stm3.append("assistant", "a language")
        stm3.append("user", "show code")
        stm3.append("assistant", "print('hello')")
        mgr.commit(stm3.to_json(), question="show code", answer="print('hello')")

        assert mgr.undo()["id"] == cp2
        restored = json.loads(mgr.get_head_stm())
        assert len(restored["messages"]) == 4
        assert restored["messages"][-1]["content"] == "a language"

        assert mgr.undo()["id"] == cp1
        restored = json.loads(mgr.get_head_stm())
        assert len(restored["messages"]) == 2
        assert restored["messages"][0]["content"] == "hello"

    def test_redo_restores_stm_state(self, mgr):
        stm1 = _make_stm([{"role": "user", "content": "q1"}])
        mgr.commit(stm1, question="q1", answer="a1")
        stm2 = _make_stm([{"role": "user", "content": "q1"},
                          {"role": "assistant", "content": "a1"},
                          {"role": "user", "content": "q2"}])
        cp2 = mgr.commit(stm2, question="q2", answer="a2")

        mgr.undo()
        cp_restored = mgr.redo()
        assert cp_restored is not None
        assert cp_restored["id"] == cp2

        restored = json.loads(mgr.get_head_stm())
        assert len(restored["messages"]) == 3

    def test_commit_after_undo_clears_redo(self, mgr):
        cp1 = mgr.commit(_make_stm([{"role": "user", "content": "q1"}]), question="q1", answer="a1")
        mgr.commit(_make_stm([{"role": "user", "content": "q2"}]), question="q2", answer="a2")

        mgr.undo()
        assert mgr.status()["can_redo"] is True

        mgr.commit(_make_stm([{"role": "user", "content": "q3"}]), question="q3", answer="a3")
        assert mgr.status()["can_redo"] is False
        assert mgr.status()["head"]["question"] == "q3"

        assert mgr.undo()["id"] == cp1

    def test_undo_at_root_returns_none(self, mgr):
        mgr.commit(_make_stm(), question="q1", answer="a1")
        mgr.commit(_make_stm(), question="q2", answer="a2")
        assert mgr.undo() is not None
        assert mgr.undo() is None
        assert mgr.undo() is None

    def test_status_reflects_undo_redo_availability(self, mgr):
        st = mgr.status()
        assert st["can_undo"] is False
        assert st["can_redo"] is False

        mgr.commit(_make_stm(), question="q1", answer="a1")
        st = mgr.status()
        assert st["can_undo"] is False
        assert st["can_redo"] is False

        mgr.commit(_make_stm(), question="q2", answer="a2")
        st = mgr.status()
        assert st["can_undo"] is True
        assert st["can_redo"] is False

        mgr.undo()
        st = mgr.status()
        assert st["can_undo"] is False
        assert st["can_redo"] is True

    def test_log_after_rollback_shows_correct_chain(self, mgr):
        mgr.commit(_make_stm([{"role": "user", "content": "q1"}]), question="q1", answer="a1")
        mgr.commit(_make_stm([{"role": "user", "content": "q2"}]), question="q2", answer="a2")
        mgr.commit(_make_stm([{"role": "user", "content": "q3"}]), question="q3", answer="a3")

        mgr.undo()
        entries = mgr.log()
        assert len(entries) == 2
        assert entries[0]["is_head"] is True
        assert entries[0]["question"] == "q2"
        assert entries[1]["question"] == "q1"

    def test_multi_step_undo_redo_cycle(self, mgr):
        ids = []
        for i in range(4):
            ids.append(mgr.commit(
                _make_stm([{"role": "user", "content": f"q{i}"}]),
                question=f"q{i}", answer=f"a{i}",
            ))

        assert mgr.undo()["id"] == ids[2]
        assert mgr.undo()["id"] == ids[1]
        assert mgr.undo()["id"] == ids[0]

        assert mgr.redo()["id"] == ids[1]
        assert mgr.redo()["id"] == ids[2]

        assert mgr.status()["head"]["id"] == ids[2]
        assert mgr.status()["can_redo"] is True
        assert mgr.status()["can_undo"] is True


class TestSessionBranchingFlow:
    """Integration tests for branch creation, switching, and state independence."""

    def test_branch_creates_fork_at_current_head(self, mgr):
        mgr.commit(_make_stm([{"role": "user", "content": "q1"}]), question="q1", answer="a1")
        mgr.commit(_make_stm([{"role": "user", "content": "q2"}]), question="q2", answer="a2")

        mgr.branch("experiment")
        status = mgr.status()
        assert status["branch"] == "experiment"
        assert status["head"]["question"] == "q2"

    def test_branches_have_independent_commits(self, mgr):
        mgr.commit(_make_stm([{"role": "user", "content": "q1"}]), question="q1", answer="a1")

        mgr.branch("experiment")
        mgr.commit(_make_stm([{"role": "user", "content": "q2-exp"}]), question="q2-exp", answer="a2-exp")

        mgr.checkout("main")
        main_entries = mgr.log()
        assert len(main_entries) == 1
        assert main_entries[0]["question"] == "q1"

        mgr.checkout("experiment")
        exp_entries = mgr.log()
        assert len(exp_entries) == 2
        assert exp_entries[0]["question"] == "q2-exp"

    def test_checkout_by_id_switches_branch(self, mgr):
        cp1 = mgr.commit(_make_stm([{"role": "user", "content": "q1"}]), question="q1", answer="a1")
        mgr.commit(_make_stm([{"role": "user", "content": "q2"}]), question="q2", answer="a2")

        cp = mgr.checkout(cp1)
        assert cp is not None
        assert cp["id"] == cp1
        assert mgr.status()["head"]["id"] == cp1

    def test_branch_from_mid_history(self, mgr):
        """checkout by ID moves current branch HEAD to that checkpoint."""
        mgr.commit(_make_stm([{"role": "user", "content": "q1"}]), question="q1", answer="a1")
        cp2 = mgr.commit(_make_stm([{"role": "user", "content": "q2"}]), question="q2", answer="a2")
        mgr.commit(_make_stm([{"role": "user", "content": "q3"}]), question="q3", answer="a3")

        mgr.checkout(cp2)
        mgr.branch("feature")
        mgr.commit(_make_stm([{"role": "user", "content": "q4-feat"}]), question="q4-feat", answer="a4-feat")

        feat_entries = mgr.log()
        assert len(feat_entries) == 3
        assert feat_entries[0]["question"] == "q4-feat"

        mgr.checkout("main")
        main_entries = mgr.log()
        assert len(main_entries) == 2
        assert main_entries[0]["question"] == "q2"

        all_entries = mgr.log(all_branches=True)
        assert len(all_entries) == 4

    def test_multi_branch_independence(self, mgr):
        mgr.commit(_make_stm([{"role": "user", "content": "q1"}]), question="q1", answer="a1")

        for br in ["a", "b", "c"]:
            mgr.branch(br)
            mgr.commit(
                _make_stm([{"role": "user", "content": f"q2-{br}"}]),
                question=f"q2-{br}", answer=f"a2-{br}",
            )
            mgr.checkout("main")

        for br in ["a", "b", "c"]:
            mgr.checkout(br)
            entries = mgr.log()
            assert len(entries) == 2, f"branch {br} expected 2 entries, got {len(entries)}"
            assert entries[0]["question"] == f"q2-{br}"

    def test_log_all_branches_shows_everything(self, mgr):
        mgr.commit(_make_stm([{"role": "user", "content": "q1"}]), question="q1", answer="a1")

        mgr.branch("exp1")
        mgr.commit(_make_stm([{"role": "user", "content": "q2-exp1"}]), question="q2-exp1", answer="a2-exp1")

        mgr.checkout("main")
        mgr.branch("exp2")
        mgr.commit(_make_stm([{"role": "user", "content": "q2-exp2"}]), question="q2-exp2", answer="a2-exp2")

        all_entries = mgr.log(all_branches=True)
        assert len(all_entries) == 3

    def test_branch_then_undo_redo(self, mgr):
        mgr.commit(_make_stm([{"role": "user", "content": "q1"}]), question="q1", answer="a1")
        mgr.branch("feature")
        cp2 = mgr.commit(_make_stm([{"role": "user", "content": "q2"}]), question="q2", answer="a2")
        mgr.commit(_make_stm([{"role": "user", "content": "q3"}]), question="q3", answer="a3")

        mgr.undo()
        assert mgr.status()["head"]["id"] == cp2
        mgr.redo()
        assert mgr.status()["head"]["question"] == "q3"

    def test_checkout_after_commits_on_different_branches(self, mgr):
        mgr.commit(_make_stm([{"role": "user", "content": "root"}]), question="root", answer="root-ans")
        mgr.branch("feat1")
        mgr.commit(_make_stm([{"role": "user", "content": "f1"}]), question="f1", answer="f1-ans")

        mgr.checkout("main")
        mgr.branch("feat2")
        mgr.commit(_make_stm([{"role": "user", "content": "f2"}]), question="f2", answer="f2-ans")

        mgr.checkout("feat1")
        assert mgr.status()["head"]["question"] == "f1"

        mgr.checkout("feat2")
        assert mgr.status()["head"]["question"] == "f2"

        mgr.checkout("main")
        assert mgr.status()["head"]["question"] == "root"


class TestBranchLtmIsolation:
    """Integration tests for LTM isolation between branches.

    NOTE: checkout() cleanup deletes LTM refs of diverged checkpoints.
    This means after switching away from a branch, its HEAD checkpoint's
    LTM refs are gone. Tests account for this behavior.

    Requires the long_term_memories table (provided by mgr_with_ltm fixture).
    """

    def _head_ltm_ids(self, mgr):
        """Return LTM IDs directly from checkpoint_ltm_refs for the HEAD checkpoint."""
        head = mgr._current_checkpoint()
        if head is None:
            return []
        rows = mgr._conn.execute(
            "SELECT ltm_memory_id FROM checkpoint_ltm_refs WHERE checkpoint_id = ?",
            (head["id"],),
        ).fetchall()
        return [r["ltm_memory_id"] for r in rows]

    def test_ltm_refs_per_branch_head(self, mgr_with_ltm):
        """Each branch's HEAD checkpoint stores its own LTM refs."""
        mgr = mgr_with_ltm
        mgr.commit(
            _make_stm([{"role": "user", "content": "q1"}]),
            question="q1", answer="a1", new_ltm_ids=[10, 20],
        )

        assert set(self._head_ltm_ids(mgr)) == {10, 20}

        mgr.branch("experiment")
        mgr.commit(
            _make_stm([{"role": "user", "content": "q2"}]),
            question="q2", answer="a2", new_ltm_ids=[30, 40],
        )

        assert set(self._head_ltm_ids(mgr)) == {30, 40}

        mgr.checkout("main")
        assert set(self._head_ltm_ids(mgr)) == {10, 20}

    def test_undo_with_two_commits_clears_ltm_ref(self, mgr_with_ltm):
        """Undo with a parent checkpoint clears the undone checkpoint's LTM refs."""
        mgr = mgr_with_ltm
        mgr.commit(
            _make_stm([{"role": "user", "content": "q1"}]),
            question="q1", answer="a1", new_ltm_ids=[100],
        )
        mgr.commit(
            _make_stm([{"role": "user", "content": "q2"}]),
            question="q2", answer="a2", new_ltm_ids=[200],
        )

        rows = mgr._conn.execute(
            "SELECT ltm_memory_id FROM checkpoint_ltm_refs"
        ).fetchall()
        assert len(rows) == 2

        mgr.undo()

        rows = mgr._conn.execute(
            "SELECT ltm_memory_id FROM checkpoint_ltm_refs"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["ltm_memory_id"] == 100

    def test_undo_with_two_commits_deletes_ltm_row(self, mgr_with_ltm):
        """Undo physically deletes LTM rows referenced by the undone checkpoint."""
        mgr = mgr_with_ltm
        mgr._conn.execute(
            "INSERT INTO long_term_memories (id, session_id, content, category) "
            "VALUES (101, 'test-session', 'memory 101', 'general')"
        )
        mgr._conn.execute(
            "INSERT INTO long_term_memories (id, session_id, content, category) "
            "VALUES (102, 'test-session', 'memory 102', 'general')"
        )
        mgr._conn.commit()

        mgr.commit(
            _make_stm([{"role": "user", "content": "q1"}]),
            question="q1", answer="a1", new_ltm_ids=[101],
        )
        mgr.commit(
            _make_stm([{"role": "user", "content": "q2"}]),
            question="q2", answer="a2", new_ltm_ids=[102],
        )

        assert len(mgr._conn.execute(
            "SELECT id FROM long_term_memories WHERE id = 102"
        ).fetchall()) == 1

        mgr.undo()
        assert len(mgr._conn.execute(
            "SELECT id FROM long_term_memories WHERE id = 102"
        ).fetchall()) == 0, "undo should physically delete the LTM row"
        assert len(mgr._conn.execute(
            "SELECT id FROM long_term_memories WHERE id = 101"
        ).fetchall()) == 1, "parent checkpoint's LTM row should remain"

    def test_ltm_refs_track_branch_switch(self, mgr_with_ltm):
        """Branch switching preserves correct per-branch HEAD LTM refs."""
        mgr = mgr_with_ltm
        mgr.commit(
            _make_stm([{"role": "user", "content": "q1"}]),
            question="q1", answer="a1", new_ltm_ids=[1],
        )
        mgr.commit(
            _make_stm([{"role": "user", "content": "q2"}]),
            question="q2", answer="a2", new_ltm_ids=[2],
        )

        mgr.branch("experiment")
        mgr.commit(
            _make_stm([{"role": "user", "content": "q3-exp"}]),
            question="q3-exp", answer="a3-exp", new_ltm_ids=[3],
        )

        assert set(self._head_ltm_ids(mgr)) == {3}

        mgr.checkout("main")
        assert set(self._head_ltm_ids(mgr)) == {2}

        mgr.undo()
        assert set(self._head_ltm_ids(mgr)) == {1}

    def test_checkout_removes_diverged_ltm_rows(self, mgr_with_ltm):
        """checkout physically deletes LTM rows from the diverged path."""
        mgr = mgr_with_ltm
        mgr._conn.execute(
            "INSERT INTO long_term_memories (id, session_id, content, category) "
            "VALUES (50, 'test-session', 'main-only', 'general')"
        )
        mgr._conn.commit()

        mgr.commit(
            _make_stm([{"role": "user", "content": "q1"}]),
            question="q1", answer="a1", new_ltm_ids=[50],
        )

        mgr.branch("feature")
        mgr._conn.execute(
            "INSERT INTO long_term_memories (id, session_id, content, category) "
            "VALUES (60, 'test-session', 'feature-memory', 'general')"
        )
        mgr._conn.commit()
        mgr.commit(
            _make_stm([{"role": "user", "content": "q2"}]),
            question="q2", answer="a2", new_ltm_ids=[60],
        )

        assert set(self._head_ltm_ids(mgr)) == {60}

        rows = mgr._conn.execute(
            "SELECT id FROM long_term_memories WHERE id IN (50, 60)"
        ).fetchall()
        assert {r["id"] for r in rows} == {50, 60}

        mgr.checkout("main")
        assert set(self._head_ltm_ids(mgr)) == {50}

        rows = mgr._conn.execute(
            "SELECT id FROM long_term_memories WHERE id IN (50, 60)"
        ).fetchall()
        assert {r["id"] for r in rows} == {50}, "feature's LTM row should be deleted"
