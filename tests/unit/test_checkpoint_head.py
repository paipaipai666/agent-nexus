"""HEAD-aware journal reads and jump_to — regression for session-scoped checkpoints.

get_messages must hide journal rows past HEAD after undo/jump, and jump_to
must support both backward (ancestor walk) and forward (redo stack) jumps.
"""
from agentnexus.memory.versioned import ConversationVersionManager


def _make_manager(tmp_path, sid="s1"):
    return ConversationVersionManager(sid, str(tmp_path / "mem.db"))


def _three_turns(mgr):
    for i in range(3):
        mgr.commit_with_messages(
            [
                {"role": "user", "content": f"q{i}", "ts": float(i)},
                {"role": "system", "content": f"[最终答案] a{i}", "ts": float(i) + 0.5},
            ],
            question=f"q{i}", answer=f"a{i}",
        )
    return [c["id"] for c in mgr.log()]  # newest first


def test_undo_hides_journal_past_head(tmp_path):
    mgr = _make_manager(tmp_path)
    tip, mid, root = _three_turns(mgr)
    assert len(mgr.get_messages()) == 6

    mgr.undo()
    assert len(mgr.get_messages()) == 4  # mid checkpoint's message_count

    mgr.undo()
    assert len(mgr.get_messages()) == 2  # root checkpoint's message_count

    mgr.redo()
    assert len(mgr.get_messages()) == 4


def test_jump_backward_and_forward(tmp_path):
    mgr = _make_manager(tmp_path)
    tip, mid, root = _three_turns(mgr)

    # backward: bypassed checkpoints stay redoable, nearest pops first
    assert mgr.jump_to(root)["id"] == root
    assert len(mgr.get_messages()) == 2
    assert mgr.redo()["id"] == mid

    # forward: jumping to a redo-stack checkpoint settles on its timeline
    # and clears redo (like a new commit)
    mgr.undo()  # HEAD=root, redo=[mid, tip]
    assert mgr.jump_to(tip)["id"] == tip
    assert len(mgr.get_messages()) == 6
    assert not mgr.status()["can_redo"]

    # a checkpoint neither on the chain nor in redo is rejected
    assert mgr.jump_to("deadbeef") is None
    assert mgr.status()["head"]["id"] == tip  # HEAD untouched


def test_jump_rejects_foreign_checkpoint(tmp_path):
    mgr = _make_manager(tmp_path)
    tip, mid, root = _three_turns(mgr)
    other_dir = tmp_path / "other"
    other_dir.mkdir()
    other = _make_manager(other_dir, sid="s2")
    other_tip = _three_turns(other)[0]

    assert mgr.jump_to(other_tip) is None
    assert mgr.status()["head"]["id"] == tip  # HEAD untouched
    assert mgr.jump_to("deadbeef") is None


def test_null_message_count_means_no_truncation(tmp_path):
    # Legacy checkpoints (pre message_count column) have NULL — reads must
    # show the full journal rather than truncate to nothing.
    mgr = _make_manager(tmp_path)
    _three_turns(mgr)
    mgr._conn.execute(
        "UPDATE conversation_checkpoints SET message_count = NULL WHERE session_id = ?",
        (mgr.session_id,),
    )
    mgr._conn.commit()
    assert len(mgr.get_messages()) == 6


def test_new_commit_clears_redo_and_truncates(tmp_path):
    mgr = _make_manager(tmp_path)
    tip, mid, root = _three_turns(mgr)
    mgr.jump_to(root)
    mgr.commit_with_messages(
        [{"role": "user", "content": "q3", "ts": 9.0}], question="q3", answer="a3"
    )
    assert not mgr.status()["can_redo"]
    assert len(mgr.get_messages()) == 3  # root(2) + new turn(1)
