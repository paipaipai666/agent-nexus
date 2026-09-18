"""Transfer-table-driven FSM engine. Pure mechanical — no agent logic."""

import logging
from collections import deque
from typing import Callable

from agentnexus.agents.exceptions import AgentCancelled, FSMError
from agentnexus.agents.react_types import (
    ExecutionContext,
    ReActEvent,
    ReActEventType,
    ReActState,
    Transition,
)

logger = logging.getLogger(__name__)


class StateMachine:
    """Event-driven FSM driven by a transition table.

    Usage:
        fsm = StateMachine(table, handlers)
        fsm.subscribe(my_observer)
        fsm.run_loop(ReActEvent(ReActEventType.START, ...), ctx)
    """

    def __init__(self, table: list[Transition]):
        self._table = table
        # Indexed lookup — exact (state, event) first, then the state's
        # unconditional (event=None) transition. Table order no longer matters.
        self._by_key: dict[tuple[ReActState, ReActEventType | None], Transition] = {}
        for t in table:
            key = (t.state, t.event)
            if key in self._by_key:
                raise FSMError(f"duplicate transition for {t.state.name}+{t.event}")
            self._by_key[key] = t
        self._queue: deque[ReActEvent] = deque()
        self._observers: list[Callable[[ReActEvent, ReActState, ReActState], None]] = []
        self._state = ReActState.INIT

    @property
    def current_state(self) -> ReActState:
        return self._state

    def subscribe(self, observer: Callable[[ReActEvent, ReActState, ReActState], None]):
        """Register a callback invoked on every transition: (event, from_state, to_state)."""
        self._observers.append(observer)

    def _notify(self, event: ReActEvent, from_state: ReActState, to_state: ReActState):
        for obs in self._observers:
            try:
                obs(event, from_state, to_state)
            except Exception as e:
                logger.debug("Observer error in FSM transition %s -> %s: %s", from_state, to_state, e)

    def _lookup(self, event: ReActEvent) -> Transition | None:
        """Find the transition for (current_state, event.type), falling back to
        the state's unconditional transition (event=None) if one exists."""
        return (self._by_key.get((self._state, event.type))
                or self._by_key.get((self._state, None)))

    def run_loop(self, initial_event: ReActEvent, ctx: ExecutionContext,
                 handlers: dict) -> tuple[str | None, list]:
        """Process events until DONE state.

        Returns (last_answer, steps) — the same contract as ReActAgent.run().
        """
        missing = {t.handler for t in self._table} - set(handlers)
        if missing:
            raise FSMError(f"missing handlers: {sorted(missing)}")

        self._state = ReActState.INIT
        self._queue.clear()
        initial_event.seq = ctx.next_seq()
        self._queue.append(initial_event)

        while True:
            self._raise_if_cancelled(ctx)
            if self._queue:
                event = self._queue.popleft()
            elif not self._try_auto_advance(ctx, handlers):
                break  # no events and no unconditional transitions → exit
            else:
                continue  # auto-advance consumed, re-check queue

            t = self._lookup(event)
            if t is None:
                raise FSMError(f"no transition for {self._state.name}+{event.type.name}")

            from_state = self._state
            self._state = t.next_state
            self._notify(event, from_state, t.next_state)

            # Invoke handler BEFORE DONE check — handler may set ctx.last_answer
            handler_fn = handlers.get(t.handler)
            if handler_fn:
                self._raise_if_cancelled(ctx)
                new_events = handler_fn(ctx, event)
                self._raise_if_cancelled(ctx)
                if new_events:
                    for ne in new_events:
                        ne.step_id = ctx.current_step
                        ne.seq = ctx.next_seq()
                        self._queue.append(ne)

            if t.next_state == ReActState.DONE:
                return (ctx.last_answer, ctx.steps)

        if self._state != ReActState.DONE:
            logger.error("FSM exited in non-terminal state: %s", self._state)
            if not ctx.last_answer:
                ctx.last_answer = f"[Agent exited in state {self._state.name}]"

        return (ctx.last_answer, ctx.steps)

    def _try_auto_advance(self, ctx, handlers) -> bool:
        """If no events are queued, try an unconditional transition (event=None).

        Returns True if an unconditional transition was found and fired.
        """
        self._raise_if_cancelled(ctx)
        t = self._by_key.get((self._state, None))
        if t is None:
            return False

        from_state = self._state
        self._state = t.next_state
        self._notify(None, from_state, t.next_state)

        handler_fn = handlers.get(t.handler)
        if handler_fn:
            self._raise_if_cancelled(ctx)
            new_events = handler_fn(ctx, None)
            self._raise_if_cancelled(ctx)
            if new_events:
                for ne in new_events:
                    ne.step_id = ctx.current_step
                    ne.seq = ctx.next_seq()
                    self._queue.append(ne)

        return True

    @staticmethod
    def _raise_if_cancelled(ctx) -> None:
        checker = getattr(ctx, "cancel_checker", None)
        if checker is not None and checker():
            raise AgentCancelled("cancelled")
