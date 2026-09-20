"""Tests for UI-neutral turn runtime."""

from unittest.mock import MagicMock

from agentnexus.services.turn import TurnRuntime


def _turn(memory=None, version=None):
    return TurnRuntime(
        run_id="run_1",
        session_id="session_1",
        question="do work",
        memory_manager=memory,
        version_manager=version,
    )


def test_finish_persists_checkpoint_without_extra_memory_append():
    memory = MagicMock()
    memory.short_term.to_json.return_value = '{"messages":[]}'
    version = MagicMock()
    turn = _turn(memory, version)

    record = turn.finish("done")

    assert record.status == "finished"
    assert record.answer == "done"
    # finish() no longer appends to memory — the agent already stores
    # the answer as "[最终答案]" in re_act_agent.py:_on_emit_answer.
    memory.append.assert_not_called()
    version.commit_with_messages.assert_called_once()


def test_cancel_generates_summary_with_reason_question_and_journal():
    memory = MagicMock()
    memory.short_term.to_json.return_value = '{"messages":[]}'
    turn = _turn(memory, MagicMock())
    turn.record("tool start", "web_search")

    record = turn.cancel("user interrupted")

    assert record.status == "interrupted"
    assert "user interrupted" in record.answer
    assert "do work" in record.answer
    assert "tool start: web_search" in record.answer
    # cancel() does not append to memory (only finish does)
    memory.append.assert_not_called()


def test_fail_generates_summary_with_detail():
    memory = MagicMock()
    memory.short_term.to_json.return_value = '{"messages":[]}'
    turn = _turn(memory, MagicMock())

    record = turn.fail("network error", "timeout")

    assert record.status == "failed"
    assert "network error" in record.answer
    assert "timeout" in record.answer


def test_cancel_is_idempotent_for_persistence():
    memory = MagicMock()
    memory.short_term.to_json.return_value = '{"messages":[]}'
    version = MagicMock()
    turn = _turn(memory, version)

    turn.cancel("first")
    turn.cancel("second")

    # cancel() does not append to memory (only finish does)
    memory.append.assert_not_called()
    version.commit_with_messages.assert_called_once()


def test_memory_append_failure_does_not_block_checkpoint():
    memory = MagicMock()
    memory.append.side_effect = RuntimeError("memory failed")
    memory.short_term.to_json.return_value = '{"messages":[]}'
    version = MagicMock()
    turn = _turn(memory, version)

    turn.cancel("cancelled")

    version.commit_with_messages.assert_called_once()


def _committed_messages(version):
    call = version.commit_with_messages.call_args
    return call.kwargs.get("messages", call[1].get("messages", []))


def test_cancel_commits_cancelled_marker_message():
    """决策 5：被取消的 turn 必须在历史中留下'已取消'标注。"""
    memory = MagicMock()
    memory.short_term.to_json.return_value = '{"messages":[]}'
    memory.short_term.get_all.return_value = [{"role": "user", "content": "do work"}]
    version = MagicMock()
    version.get_message_count.return_value = 0
    turn = _turn(memory, version)

    record = turn.cancel("user interrupted")

    assert record.status == "interrupted"
    messages = _committed_messages(version)
    assert any(
        m.get("role") == "assistant" and "已取消" in m.get("content", "")
        for m in messages
    ), f"cancelled marker missing from committed messages: {messages}"


def test_cancel_marker_includes_reason():
    memory = MagicMock()
    memory.short_term.to_json.return_value = '{"messages":[]}'
    memory.short_term.get_all.return_value = []
    version = MagicMock()
    version.get_message_count.return_value = 0
    turn = _turn(memory, version)

    turn.cancel("client_disconnected")

    messages = _committed_messages(version)
    marker = next(m for m in messages if "已取消" in m.get("content", ""))
    assert "client_disconnected" in marker["content"]


def test_finish_does_not_commit_cancelled_marker():
    memory = MagicMock()
    memory.short_term.to_json.return_value = '{"messages":[]}'
    memory.short_term.get_all.return_value = []
    version = MagicMock()
    version.get_message_count.return_value = 0
    turn = _turn(memory, version)

    turn.finish("done")

    messages = _committed_messages(version)
    assert not any("已取消" in m.get("content", "") for m in messages)
