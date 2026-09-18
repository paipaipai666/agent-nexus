"""Tests for the transfer-table-driven StateMachine FSM engine."""
from unittest.mock import MagicMock

import pytest

from agentnexus.agents.exceptions import AgentCancelled, FSMError
from agentnexus.agents.fsm import StateMachine
from agentnexus.agents.react_types import (
    ExecutionContext,
    ReActEvent,
    ReActEventType,
    ReActState,
    Transition,
)


class TestStateMachineInit:
    """Verify construction and initial conditions."""

    def test_initial_state_is_init(self):
        fsm = StateMachine(table=[])
        assert fsm.current_state == ReActState.INIT

    def test_stores_table(self):
        table = [Transition(ReActState.INIT, ReActEventType.START, ReActState.DONE, "nop")]
        fsm = StateMachine(table=table)
        assert fsm._table is table

    def test_empty_table_can_be_created(self):
        fsm = StateMachine(table=[])
        assert fsm._table == []


class TestStateMachineDispatch:
    """Test the _lookup dispatch logic.

    Note: handlers for transitions landing on DONE are never invoked
    because the FSM returns before calling the handler.  Use non-DONE
    next states when verifying handler invocation.
    """

    def test_matching_transition_found_and_handler_invoked(self):
        handler = MagicMock(return_value=[])
        table = [
            Transition(ReActState.INIT, ReActEventType.START, ReActState.SELECT_STRATEGY, "my_handler"),
        ]
        fsm = StateMachine(table=table)
        ctx = ExecutionContext(question="test")
        event = ReActEvent(ReActEventType.START)

        fsm.run_loop(event, ctx, {"my_handler": handler})

        handler.assert_called_once()
        args, _ = handler.call_args
        assert args[0] is ctx
        assert args[1] is event

    def test_unknown_event_raises(self):
        """An event with no matching transition is a table bug, not a silent skip."""
        handler = MagicMock(return_value=[])
        table = [
            Transition(ReActState.INIT, ReActEventType.START, ReActState.DONE, "my_handler"),
        ]
        fsm = StateMachine(table=table)
        ctx = ExecutionContext(question="test")
        event = ReActEvent(ReActEventType.LLM_ERROR)

        with pytest.raises(FSMError):
            fsm.run_loop(event, ctx, {"my_handler": handler})

        handler.assert_not_called()
        assert fsm.current_state == ReActState.INIT

    def test_unconditional_transition_always_matches(self):
        """A transition with event=None fires regardless of the incoming event type."""
        handler = MagicMock(return_value=[])
        table = [
            Transition(ReActState.INIT, None, ReActState.SELECT_STRATEGY, "catch_all"),
        ]
        fsm = StateMachine(table=table)
        ctx = ExecutionContext(question="test")

        # Fire with an arbitrary event type
        fsm.run_loop(ReActEvent(ReActEventType.LLM_ERROR), ctx, {"catch_all": handler})

        handler.assert_called_once()
        assert fsm.current_state == ReActState.SELECT_STRATEGY

    def test_missing_handler_raises(self):
        """A transition whose handler name is absent from the handlers dict is a bug."""
        table = [
            Transition(ReActState.INIT, ReActEventType.START, ReActState.SELECT_STRATEGY, "missing"),
        ]
        fsm = StateMachine(table=table)
        ctx = ExecutionContext(question="test")

        with pytest.raises(FSMError):
            fsm.run_loop(ReActEvent(ReActEventType.START), ctx, {})

    def test_duplicate_transition_rejected(self):
        """Duplicate (state, event) rows are a table bug — rejected at construction."""
        table = [
            Transition(ReActState.INIT, ReActEventType.START, ReActState.INIT, "handler_a"),
            Transition(ReActState.INIT, ReActEventType.START, ReActState.ERROR_ABORT, "handler_b"),
        ]
        with pytest.raises(FSMError):
            StateMachine(table=table)


class TestStateMachineRunLoop:
    """Test multi-step transition chains via run_loop.

    Key FSM contract: when a transition lands on DONE the handler is
    skipped -- the answer must be prepared in a predecessor handler.
    """

    def test_simple_three_step_chain(self):
        """INIT -> SELECT_STRATEGY -> EMIT_ANSWER -> DONE."""
        calls = []

        def to_strategy(ctx, event):
            calls.append("strategy")
            ctx.current_step = 1
            return [ReActEvent(ReActEventType.STRATEGY_READY)]

        def to_answer(ctx, event):
            calls.append("answer")
            ctx.last_answer = "hello"
            return [ReActEvent(ReActEventType.FALLBACK_TEXT)]

        table = [
            Transition(ReActState.INIT, ReActEventType.START, ReActState.SELECT_STRATEGY, "to_strategy"),
            Transition(ReActState.SELECT_STRATEGY, ReActEventType.STRATEGY_READY, ReActState.EMIT_ANSWER, "to_answer"),
            Transition(ReActState.EMIT_ANSWER, ReActEventType.FALLBACK_TEXT, ReActState.DONE, "nop"),
        ]
        fsm = StateMachine(table=table)
        ctx = ExecutionContext(question="test")

        answer, steps = fsm.run_loop(
            ReActEvent(ReActEventType.START), ctx,
            {"to_strategy": to_strategy, "to_answer": to_answer, "nop": lambda c, e: []},
        )

        assert calls == ["strategy", "answer"]
        assert ctx.current_step == 1
        assert answer == "hello"
        assert steps == []

    def test_returns_last_answer_and_steps_from_ctx(self):
        """run_loop returns (ctx.last_answer, ctx.steps) so it matches ReActAgent contract."""
        handler = MagicMock(return_value=[])
        # Non-DONE so handler is actually called
        table = [Transition(ReActState.INIT, ReActEventType.START, ReActState.SELECT_STRATEGY, "h")]
        fsm = StateMachine(table=table)
        ctx = ExecutionContext(question="test", last_answer="final result", steps=["s1", "s2"])

        answer, steps = fsm.run_loop(ReActEvent(ReActEventType.START), ctx, {"h": handler})

        assert answer == "final result"
        assert steps == ["s1", "s2"]

    def test_handler_emits_multiple_events_processed_in_order(self):
        """Handlers can enqueue multiple events; each is consumed in FIFO order."""
        events = []

        def first_handler(ctx, event):
            events.append("first")
            # Enqueue two events consumed in FIFO order
            return [ReActEvent(ReActEventType.STRATEGY_READY), ReActEvent(ReActEventType.STRATEGY_READY)]

        def second_handler(ctx, event):
            events.append("second")
            return [ReActEvent(ReActEventType.FALLBACK_TEXT)]

        def final_handler(ctx, event):
            events.append("final")
            ctx.last_answer = "done"
            return [ReActEvent(ReActEventType.FALLBACK_TEXT)]

        table = [
            Transition(ReActState.INIT, ReActEventType.START, ReActState.SELECT_STRATEGY, "first"),
            # Loop back so both STRATEGY_READY events are consumed by second_handler
            Transition(ReActState.SELECT_STRATEGY, ReActEventType.STRATEGY_READY, ReActState.SELECT_STRATEGY, "second"),
            Transition(ReActState.SELECT_STRATEGY, ReActEventType.FALLBACK_TEXT, ReActState.EMIT_ANSWER, "final"),
            Transition(ReActState.EMIT_ANSWER, ReActEventType.FALLBACK_TEXT, ReActState.DONE, "nop"),
        ]
        fsm = StateMachine(table=table)
        ctx = ExecutionContext(question="test")

        answer, _ = fsm.run_loop(
            ReActEvent(ReActEventType.START), ctx,
            {"first": first_handler, "second": second_handler, "final": final_handler,
             "nop": lambda c, e: []},
        )

        # first -> second (1st SR) -> second (2nd SR) -> final (1st FT) -> DONE (2nd FT, handler skipped)
        assert events == ["first", "second", "second", "final"]
        assert answer == "done"

    def test_handler_returning_none_is_ok(self):
        """A handler that returns None should not crash the loop."""
        def nop_handler(ctx, event):
            return None  # explicit None

        table = [
            Transition(ReActState.INIT, ReActEventType.START, ReActState.SELECT_STRATEGY, "nop"),
        ]
        fsm = StateMachine(table=table)
        ctx = ExecutionContext(question="test")

        answer, steps = fsm.run_loop(ReActEvent(ReActEventType.START), ctx, {"nop": nop_handler})
        assert fsm.current_state == ReActState.SELECT_STRATEGY
        # FSM now returns an error message when exiting in non-terminal state
        assert answer is not None
        assert "exited in state" in answer

    def test_event_step_id_set_automatically(self):
        """Enqueued events get their step_id set to ctx.current_step."""
        def strategy_handler(ctx, event):
            ctx.current_step = 42
            return [ReActEvent(ReActEventType.STRATEGY_READY)]

        def answer_handler(ctx, event):
            assert event.step_id == 42
            ctx.last_answer = "ok"
            return [ReActEvent(ReActEventType.FALLBACK_TEXT)]

        table = [
            Transition(ReActState.INIT, ReActEventType.START, ReActState.SELECT_STRATEGY, "strategy"),
            Transition(ReActState.SELECT_STRATEGY, ReActEventType.STRATEGY_READY, ReActState.EMIT_ANSWER, "answer"),
            Transition(ReActState.EMIT_ANSWER, ReActEventType.FALLBACK_TEXT, ReActState.DONE, "nop"),
        ]
        fsm = StateMachine(table=table)
        ctx = ExecutionContext(question="test")

        fsm.run_loop(
            ReActEvent(ReActEventType.START), ctx,
            {"strategy": strategy_handler, "answer": answer_handler, "nop": lambda c, e: []},
        )
        assert ctx.last_answer == "ok"


class TestStateMachineObserver:
    """Test the subscribe/notify observer mechanism."""

    def test_observer_called_on_each_transition(self):
        observer = MagicMock()
        table = [
            Transition(ReActState.INIT, ReActEventType.START, ReActState.SELECT_STRATEGY, "h1"),
            Transition(ReActState.SELECT_STRATEGY, ReActEventType.STRATEGY_READY, ReActState.DONE, "h2"),
        ]
        fsm = StateMachine(table=table)
        fsm.subscribe(observer)

        ctx = ExecutionContext(question="test")
        fsm.run_loop(
            ReActEvent(ReActEventType.START), ctx,
            {"h1": lambda c, e: [ReActEvent(ReActEventType.STRATEGY_READY)], "h2": lambda c, e: []},
        )

        # Called twice: INIT->SELECT_STRATEGY and SELECT_STRATEGY->DONE
        assert observer.call_count == 2

    def test_observer_receives_correct_args(self):
        calls = []

        def observer(event, from_state, to_state):
            calls.append((event.type, from_state, to_state))

        table = [
            Transition(ReActState.INIT, ReActEventType.START, ReActState.SELECT_STRATEGY, "h"),
            Transition(ReActState.SELECT_STRATEGY, None, ReActState.DONE, "done"),
        ]
        fsm = StateMachine(table=table)
        fsm.subscribe(observer)

        ctx = ExecutionContext(question="test")
        fsm.run_loop(
            ReActEvent(ReActEventType.START), ctx,
            {"h": lambda c, e: [ReActEvent(ReActEventType.STRATEGY_READY)], "done": lambda c, e: []},
        )

        assert len(calls) == 2
        assert calls[0] == (ReActEventType.START, ReActState.INIT, ReActState.SELECT_STRATEGY)
        assert calls[1] == (ReActEventType.STRATEGY_READY, ReActState.SELECT_STRATEGY, ReActState.DONE)

    def test_observer_exception_does_not_crash_fsm(self):
        """An observer that raises is silently caught so the FSM keeps running."""

        def exploding_observer(event, from_state, to_state):
            raise RuntimeError("boom")

        table = [
            Transition(ReActState.INIT, ReActEventType.START, ReActState.DONE, "h"),
        ]
        fsm = StateMachine(table=table)
        fsm.subscribe(exploding_observer)

        ctx = ExecutionContext(question="test")
        fsm.run_loop(ReActEvent(ReActEventType.START), ctx, {"h": lambda c, e: []})

        assert fsm.current_state == ReActState.DONE

    def test_multiple_observers_all_notified(self):
        o1 = MagicMock()
        o2 = MagicMock()
        table = [Transition(ReActState.INIT, ReActEventType.START, ReActState.DONE, "h")]
        fsm = StateMachine(table=table)
        fsm.subscribe(o1)
        fsm.subscribe(o2)

        ctx = ExecutionContext(question="test")
        fsm.run_loop(ReActEvent(ReActEventType.START), ctx, {"h": lambda c, e: []})

        o1.assert_called_once()
        o2.assert_called_once()

    def test_mixed_observers_some_fail_some_succeed(self):
        """When one observer fails, others still get notified."""
        o_ok = MagicMock()

        def o_bad(event, from_state, to_state):
            raise ValueError("fail")

        table = [Transition(ReActState.INIT, ReActEventType.START, ReActState.DONE, "h")]
        fsm = StateMachine(table=table)
        fsm.subscribe(o_bad)
        fsm.subscribe(o_ok)

        ctx = ExecutionContext(question="test")
        fsm.run_loop(ReActEvent(ReActEventType.START), ctx, {"h": lambda c, e: []})

        o_ok.assert_called_once()


class TestStateMachineDone:
    """Test that DONE state terminates the loop immediately.

    When a transition lands on DONE the FSM returns immediately without
    calling the handler.  Handlers and events meant for the DONE-bound
    step must be placed on the *preceding* transition.
    """

    def test_done_terminates_immediately(self):
        """When a transition lands on DONE, no further handlers or events are processed."""
        extra_handler = MagicMock()

        table = [
            Transition(ReActState.INIT, ReActEventType.START, ReActState.DONE, "done"),
            # This transition would only match if DONE didn't terminate:
            Transition(ReActState.DONE, ReActEventType.START, ReActState.SELECT_STRATEGY, "extra"),
        ]
        fsm = StateMachine(table=table)
        ctx = ExecutionContext(question="test")

        fsm.run_loop(
            ReActEvent(ReActEventType.START), ctx,
            {"done": lambda c, e: [ReActEvent(ReActEventType.START)], "extra": extra_handler},
        )

        # The handler enqueues another START, but since we land on DONE,
        # the loop returns before processing it -- so "extra" should never fire.
        extra_handler.assert_not_called()

    def test_done_returns_correct_answer(self):
        ctx = ExecutionContext(question="test", last_answer="done-answer", steps=["step1"])
        table = [Transition(ReActState.INIT, ReActEventType.START, ReActState.DONE, "h")]
        fsm = StateMachine(table=table)

        answer, steps = fsm.run_loop(ReActEvent(ReActEventType.START), ctx, {"h": lambda c, e: []})

        assert answer == "done-answer"
        assert steps == ["step1"]

    def test_direct_done_transition_from_any_state(self):
        """Transitioning directly to DONE from any non-terminal state always terminates."""
        ctx = ExecutionContext(question="test", last_answer="early-exit")

        table = [Transition(ReActState.INIT, None, ReActState.DONE, "h")]
        fsm = StateMachine(table=table)

        answer, _ = fsm.run_loop(ReActEvent(ReActEventType.START), ctx, {"h": lambda c, e: []})
        assert answer == "early-exit"


class TestStateMachineCancellation:
    def test_cancel_checker_stops_before_handler(self):
        handler = MagicMock(return_value=[])
        table = [Transition(ReActState.INIT, ReActEventType.START, ReActState.SELECT_STRATEGY, "h")]
        fsm = StateMachine(table=table)
        ctx = ExecutionContext(question="test")
        ctx.cancel_checker = lambda: True

        with pytest.raises(AgentCancelled):
            fsm.run_loop(ReActEvent(ReActEventType.START), ctx, {"h": handler})

        handler.assert_not_called()


class TestRealTransferTable:
    """Guard the production TRANSFER_TABLE against structural regressions.

    The engine intentionally does NOT validate reachability/outgoing-edges at
    construction (test tables are often fragments) — the production table is
    linted here instead.
    """

    def _stub_handlers(self, **overrides):
        from agentnexus.agents.react_transitions import TRANSFER_TABLE
        handlers = {t.handler: MagicMock(return_value=[]) for t in TRANSFER_TABLE}
        handlers.update(overrides)
        return handlers

    def test_real_transfer_table_is_well_formed(self):
        from collections import deque

        from agentnexus.agents.react_transitions import TRANSFER_TABLE

        # (a) no duplicate (state, event) rows
        keys = [(t.state, t.event) for t in TRANSFER_TABLE]
        assert len(keys) == len(set(keys)), "duplicate (state, event) transitions"

        states = {t.state for t in TRANSFER_TABLE} | {ReActState.INIT}
        # (b) every non-DONE state has at least one outgoing transition
        for s in states:
            if s is ReActState.DONE:
                continue
            assert any(t.state == s for t in TRANSFER_TABLE), f"no outgoing transition from {s.name}"
        # (d) DONE has no outgoing transitions
        assert all(t.state != ReActState.DONE for t in TRANSFER_TABLE)
        # (c) every state reachable from INIT via the edge graph
        adj: dict = {}
        for t in TRANSFER_TABLE:
            adj.setdefault(t.state, set()).add(t.next_state)
        seen = {ReActState.INIT}
        queue = deque([ReActState.INIT])
        while queue:
            cur = queue.popleft()
            for nxt in adj.get(cur, ()):
                if nxt not in seen:
                    seen.add(nxt)
                    queue.append(nxt)
        unreachable = states - seen
        assert not unreachable, f"unreachable states: {sorted(s.name for s in unreachable)}"

    def test_real_table_max_steps_abort_reaches_done(self):
        """Regression: ABORT emitted from CALL_LLM (max-steps) must reach DONE.

        Before the CALL_LLM+ABORT transition existed, the event was silently
        dropped and the loop exited non-terminal with a placeholder answer.
        """
        from agentnexus.agents.react_transitions import TRANSFER_TABLE

        on_max_steps_abort = MagicMock(return_value=[])
        handlers = self._stub_handlers(
            _on_init=MagicMock(return_value=[ReActEvent(ReActEventType.STRATEGY_READY)]),
            _on_strategy_ready=MagicMock(return_value=[ReActEvent(ReActEventType.LLM_PARAMS_READY)]),
            _on_llm_params_ready=MagicMock(return_value=[ReActEvent(ReActEventType.ABORT)]),
            _on_max_steps_abort=on_max_steps_abort,
        )
        fsm = StateMachine(table=TRANSFER_TABLE)
        ctx = ExecutionContext(question="test")

        fsm.run_loop(ReActEvent(ReActEventType.START), ctx, handlers)

        assert fsm.current_state == ReActState.DONE
        on_max_steps_abort.assert_called_once()

    def test_event_seq_strictly_increasing(self):
        """FSM-queued and emit-side-channel events share one monotonic seq."""
        from agentnexus.agents.react_transitions import TRANSFER_TABLE

        observed: list[int] = []
        fsm = StateMachine(table=TRANSFER_TABLE)
        fsm.subscribe(lambda event, _f, _t: event is not None and observed.append(event.seq))

        ctx = ExecutionContext(question="test")
        handlers = self._stub_handlers(
            _on_init=MagicMock(return_value=[ReActEvent(ReActEventType.STRATEGY_READY)]),
            _on_strategy_ready=MagicMock(return_value=[ReActEvent(ReActEventType.LLM_PARAMS_READY)]),
            _on_llm_params_ready=MagicMock(return_value=[ReActEvent(ReActEventType.ABORT)]),
        )
        fsm.run_loop(ReActEvent(ReActEventType.START), ctx, handlers)

        assert len(observed) == 4  # START, STRATEGY_READY, LLM_PARAMS_READY, ABORT
        assert observed == sorted(observed)
        assert len(set(observed)) == len(observed)
