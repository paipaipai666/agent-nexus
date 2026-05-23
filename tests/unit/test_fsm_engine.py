"""Tests for the transfer-table-driven StateMachine FSM engine."""
from unittest.mock import MagicMock

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

    def test_non_matching_event_is_ignored(self):
        handler = MagicMock(return_value=[])
        table = [
            Transition(ReActState.INIT, ReActEventType.START, ReActState.DONE, "my_handler"),
        ]
        fsm = StateMachine(table=table)
        ctx = ExecutionContext(question="test")
        event = ReActEvent(ReActEventType.LLM_ERROR)

        fsm.run_loop(event, ctx, {"my_handler": handler})

        handler.assert_not_called()
        # No transition matched -- state stays INIT
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

    def test_transition_without_handler_does_not_crash(self):
        """A transition whose handler name is not in the handlers dict is a no-op."""
        table = [
            Transition(ReActState.INIT, ReActEventType.START, ReActState.SELECT_STRATEGY, "missing"),
        ]
        fsm = StateMachine(table=table)
        ctx = ExecutionContext(question="test")

        # Should not raise even though "missing" is absent from handlers
        result = fsm.run_loop(ReActEvent(ReActEventType.START), ctx, {})

        assert fsm.current_state == ReActState.SELECT_STRATEGY
        assert result == (None, [])

    def test_first_matching_transition_wins(self):
        """When multiple transitions match, the first in the table is chosen."""
        table = [
            Transition(ReActState.INIT, ReActEventType.START, ReActState.INIT, "handler_a"),
            Transition(ReActState.INIT, ReActEventType.START, ReActState.ERROR_ABORT, "handler_b"),
        ]
        fsm = StateMachine(table=table)
        ctx = ExecutionContext(question="test")
        handler_a = MagicMock(return_value=[])
        handler_b = MagicMock(return_value=[])

        fsm.run_loop(ReActEvent(ReActEventType.START), ctx,
                     {"handler_a": handler_a, "handler_b": handler_b})

        handler_a.assert_called_once()
        handler_b.assert_not_called()


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
            {"to_strategy": to_strategy, "to_answer": to_answer},
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
            {"first": first_handler, "second": second_handler, "final": final_handler},
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
        assert answer is None

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
            {"strategy": strategy_handler, "answer": answer_handler},
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
