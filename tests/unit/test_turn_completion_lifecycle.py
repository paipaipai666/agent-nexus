"""Completed turns retain their outcome across retries and late cancellation."""

from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

from agentnexus.core.memory_contracts import TurnMemory
from agentnexus.services.turn import TurnRuntime


@dataclass
class Checkpoints:
    writes: list[dict] = field(default_factory=list)
    unavailable: bool = False

    def commit_with_messages(self, **checkpoint):
        if self.unavailable:
            raise OSError("disk unavailable")
        self.writes.append(checkpoint)


def make_turn(version):
    memory = SimpleNamespace(short_term=TurnMemory())
    turn = TurnRuntime(run_id="completion", session_id="completion", question="Finish work",
                       memory_manager=memory, version_manager=version)
    memory.short_term.append("system", "[最终答案] Work completed")
    return turn


@pytest.mark.parametrize("outcome", ["finished", "interrupted", "failed"])
def test_round_commit_after_a_saved_terminal_checkpoint_is_idempotent(outcome):
    version = Checkpoints()
    turn = make_turn(version)
    if outcome == "finished":
        turn.finish("Work completed")
    elif outcome == "interrupted":
        turn.cancel("client_disconnected")
    else:
        turn.fail("Execution failed")
    assert turn.persisted is True
    assert turn.commit_round() is True
    assert len(version.writes) == 1
    assert turn.record_snapshot.status == outcome
    assert turn.last_commit_error == ""


def test_late_cancel_does_not_set_the_completed_turns_cancellation_signal():
    version = Checkpoints()
    turn = make_turn(version)
    turn.finish("Work completed")
    record = turn.cancel("client_disconnected")
    assert record.status == "finished"
    assert record.answer == "Work completed"
    assert turn.cancel_checker() is False
    assert len(version.writes) == 1


def test_retrying_a_terminal_commit_preserves_its_question_and_answer(monkeypatch):
    monkeypatch.setattr("agentnexus.services.turn.time.sleep", lambda _: None)
    version = Checkpoints(unavailable=True)
    turn = make_turn(version)
    turn.finish("Work completed")
    assert turn.persisted is False
    assert turn.last_commit_error == "disk unavailable"
    version.unavailable = False
    assert turn.commit_round() is True
    assert turn.persisted is True
    assert version.writes[0]["question"] == "Finish work"
    assert version.writes[0]["answer"] == "Work completed"
