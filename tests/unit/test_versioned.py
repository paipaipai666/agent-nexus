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


def _make_stm(messages: list[dict] | None = None) -> str:
    stm = ShortTermMemory()
    for m in (messages or []):
        stm.append(m.get("role", "user"), m.get("content", ""))
    return stm.to_json()


class TestConversationVersionManager:
    def test_schema_created(self, mgr):
        tables = mgr._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        names = {r["name"] for r in tables}
        assert "conversation_checkpoints" in names
        assert "checkpoint_ltm_refs" in names
        assert "conversation_branches" in names

    def test_commit_creates_checkpoint(self, mgr):
        stm = _make_stm([{"role": "user", "content": "hello"}])
        cp_id = mgr.commit(stm, question="hello?", answer="world")
        assert len(cp_id) == 8

        cp = mgr._get_checkpoint(cp_id)
        assert cp is not None
        assert cp["question"] == "hello?"
        assert cp["answer"] == "world"
        assert cp["branch_name"] == "main"

    def test_commit_with_ltm_refs(self, mgr):
        stm = _make_stm()
        cp_id = mgr.commit(stm, new_ltm_ids=[1, 2, 3])
        refs = mgr._conn.execute(
            "SELECT ltm_memory_id FROM checkpoint_ltm_refs WHERE checkpoint_id = ?",
            (cp_id,),
        ).fetchall()
        assert {r["ltm_memory_id"] for r in refs} == {1, 2, 3}

    def test_parent_chain(self, mgr):
        stm1 = _make_stm([{"role": "user", "content": "q1"}])
        cp1 = mgr.commit(stm1, question="q1", answer="a1")

        stm2 = _make_stm([{"role": "user", "content": "q1"},
                          {"role": "assistant", "content": "a1"},
                          {"role": "user", "content": "q2"}])
        cp2 = mgr.commit(stm2, question="q2", answer="a2")

        cp2_row = mgr._get_checkpoint(cp2)
        assert cp2_row["parent_id"] == cp1

    def test_undo_and_redo(self, mgr):
        cp1 = mgr.commit(_make_stm([{"role": "user", "content": "q1"}]), question="q1", answer="a1")
        cp2 = mgr.commit(_make_stm([{"role": "user", "content": "q1"},
                                     {"role": "user", "content": "q2"}]), question="q2", answer="a2")

        # Undo back to cp1
        prev = mgr.undo()
        assert prev is not None
        assert prev["id"] == cp1

        # Verify HEAD moved
        status = mgr.status()
        assert status["head"]["id"] == cp1
        assert status["can_redo"] is True

        # Redo back to cp2
        restored = mgr.redo()
        assert restored is not None
        assert restored["id"] == cp2

        status = mgr.status()
        assert status["head"]["id"] == cp2
        assert status["can_redo"] is False

    def test_undo_at_root_returns_none(self, mgr):
        mgr.commit(_make_stm(), question="q1", answer="a1")
        mgr.undo()
        # Second undo should fail (at root)
        result = mgr.undo()
        assert result is None

    def test_redo_stack_cleared_on_new_commit(self, mgr):
        mgr.commit(_make_stm([{"role": "user", "content": "q1"}]), question="q1", answer="a1")
        mgr.commit(_make_stm([{"role": "user", "content": "q2"}]), question="q2", answer="a2")
        mgr.undo()  # back to cp1
        assert mgr.status()["can_redo"] is True

        # New commit after undo
        mgr.commit(_make_stm([{"role": "user", "content": "q3"}]), question="q3", answer="a3")
        assert mgr.status()["can_redo"] is False

    def test_log_returns_chain(self, mgr):
        mgr.commit(_make_stm([{"role": "user", "content": "q1"}]), question="q1", answer="a1")
        mgr.commit(_make_stm([{"role": "user", "content": "q2"}]), question="q2", answer="a2")
        mgr.commit(_make_stm([{"role": "user", "content": "q3"}]), question="q3", answer="a3")

        entries = mgr.log()
        assert len(entries) == 3
        # Newest first
        assert entries[0]["question"] == "q3"
        assert entries[0]["is_head"] is True
        assert entries[2]["question"] == "q1"

    def test_branch_and_checkout(self, mgr):
        mgr.commit(_make_stm([{"role": "user", "content": "q1"}]), question="q1", answer="a1")

        # Create branch at first checkpoint
        mgr.branch("experiment")

        # Commit on experiment branch
        cp2 = mgr.commit(_make_stm([{"role": "user", "content": "q2"}]), question="q2-exp", answer="a2-exp")

        # Checkout back to main
        main_cp = mgr.checkout("main")
        assert main_cp is not None

        # Checkout experiment branch by name
        exp_cp = mgr.checkout("experiment")
        assert exp_cp is not None
        assert exp_cp["id"] == cp2

    def test_checkout_by_id(self, mgr):
        cp1 = mgr.commit(_make_stm([{"role": "user", "content": "q1"}]), question="q1", answer="a1")
        mgr.commit(_make_stm([{"role": "user", "content": "q2"}]), question="q2", answer="a2")

        cp = mgr.checkout(cp1)
        assert cp is not None
        assert cp["id"] == cp1

    def test_diff_between_checkpoints(self, mgr):
        stm1 = _make_stm([{"role": "user", "content": "q1"}])
        cp1 = mgr.commit(stm1, question="q1", answer="a1", new_ltm_ids=[1, 2])

        stm2 = _make_stm([{"role": "user", "content": "q1"},
                          {"role": "assistant", "content": "a1"},
                          {"role": "user", "content": "q2"}])
        cp2 = mgr.commit(stm2, question="q2", answer="a2", new_ltm_ids=[3])

        result = mgr.diff(cp1, cp2)
        assert result["stm_messages_added"] == 2
        assert result["ltm_added"] == [3]
        assert sorted(result["ltm_removed"]) == [1, 2]  # cp1 had LTM 1,2; cp2 only has 3

    def test_diff_defaults_to_parent_vs_head(self, mgr):
        mgr.commit(_make_stm([{"role": "user", "content": "q1"}]), question="q1", answer="a1")
        mgr.commit(_make_stm([{"role": "user", "content": "q2"}]), question="q2", answer="a2")
        result = mgr.diff()
        assert "error" not in result

    def test_status(self, mgr):
        mgr.commit(_make_stm([{"role": "user", "content": "q1"}]), question="q1", answer="a1")
        mgr.commit(_make_stm([{"role": "user", "content": "q2"}]), question="q2", answer="a2")
        st = mgr.status()
        assert st["session_id"] == "test-session"
        assert st["branch"] == "main"
        assert st["head"]["question"] == "q2"
        assert st["can_undo"] is True  # has parent (q1)

    def test_get_head_stm_roundtrip(self, mgr):
        stm1 = ShortTermMemory()
        stm1.append("user", "hello")
        stm1.append("assistant", "hi there")
        stm1._summary = "test summary"
        json_str = stm1.to_json()

        mgr.commit(json_str, question="hello", answer="hi there")

        restored_json = mgr.get_head_stm()
        assert restored_json == json_str

        stm2 = ShortTermMemory.from_json(restored_json)
        msgs = stm2.get_all()
        assert len(msgs) == 2
        assert msgs[0]["content"] == "hello"
        assert msgs[1]["content"] == "hi there"
        assert stm2._summary == "test summary"


class TestShortTermMemorySerialization:
    def test_to_json_empty(self):
        stm = ShortTermMemory()
        data = json.loads(stm.to_json())
        assert data["messages"] == []
        assert data["summary"] == ""

    def test_to_json_with_messages(self):
        stm = ShortTermMemory()
        stm.append("user", "问题1")
        stm.append("assistant", "回答1")
        data = json.loads(stm.to_json())
        assert len(data["messages"]) == 2
        assert data["messages"][0]["role"] == "user"
        assert data["messages"][0]["content"] == "问题1"

    def test_from_json_restores_state(self):
        stm = ShortTermMemory()
        stm.append("user", "测试")
        stm._summary = "摘要"
        json_str = stm.to_json()

        restored = ShortTermMemory.from_json(json_str)
        assert len(restored.get_all()) == 1
        assert restored.get_all()[0]["content"] == "测试"
        assert restored._summary == "摘要"

    def test_from_json_respects_max_messages(self):
        stm = ShortTermMemory(max_messages=3)
        stm.append("user", "a")
        stm.append("user", "b")
        stm.append("user", "c")
        stm.append("user", "d")  # pushes 'a' out due to maxlen=3
        json_str = stm.to_json()

        # Restore with larger max (should keep all 3 from json)
        restored = ShortTermMemory.from_json(json_str, max_messages=10)
        assert len(restored.get_all()) == 3
