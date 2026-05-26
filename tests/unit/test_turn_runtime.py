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
    memory.append.assert_not_called()
    version.commit.assert_called_once()


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
    memory.append.assert_called_once_with("assistant", record.answer)


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

    memory.append.assert_called_once()
    version.commit.assert_called_once()


def test_memory_append_failure_does_not_block_checkpoint():
    memory = MagicMock()
    memory.append.side_effect = RuntimeError("memory failed")
    memory.short_term.to_json.return_value = '{"messages":[]}'
    version = MagicMock()
    turn = _turn(memory, version)

    turn.cancel("cancelled")

    version.commit.assert_called_once()
