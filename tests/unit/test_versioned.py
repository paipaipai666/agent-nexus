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

    def test_commit_creates_checkpoint(self, mgr):
        stm = _make_stm([{"role": "user", "content": "hello"}])
        cp_id = mgr.commit(stm, question="hello?", answer="world")
        assert len(cp_id) == 8

        cp = mgr._get_checkpoint(cp_id)
        assert cp is not None
        assert cp["question"] == "hello?"
        assert cp["answer"] == "world"

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

    def test_status(self, mgr):
        mgr.commit(_make_stm([{"role": "user", "content": "q1"}]), question="q1", answer="a1")
        mgr.commit(_make_stm([{"role": "user", "content": "q2"}]), question="q2", answer="a2")
        st = mgr.status()
        assert st["session_id"] == "test-session"
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

    def test_session_metadata_latest_for_workspace(self, temp_agentnexus_home):
        from agentnexus.core.config import get_settings

        workspace = temp_agentnexus_home / "project"
        workspace.mkdir()
        settings = get_settings()

        mgr1 = ConversationVersionManager(
            "tui_old",
            settings.memory_db_path,
            workspace_path=str(workspace),
            profile="tui",
        )
        mgr1.commit(_make_stm([{"role": "user", "content": "old"}]), question="old")
        mgr2 = ConversationVersionManager(
            "tui_new",
            settings.memory_db_path,
            workspace_path=str(workspace),
            profile="tui",
        )
        mgr2.commit(_make_stm([{"role": "user", "content": "new"}]), question="new")

        assert ConversationVersionManager.find_latest_session(settings.memory_db_path, str(workspace)) == "tui_new"
        assert ConversationVersionManager.session_belongs_to_workspace(
            settings.memory_db_path,
            "tui_new",
            str(workspace),
        )
        assert not ConversationVersionManager.session_belongs_to_workspace(
            settings.memory_db_path,
            "tui_new",
            str(temp_agentnexus_home),
        )

        mgr1._conn.close()
        mgr2._conn.close()


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
