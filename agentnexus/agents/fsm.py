"""Transfer-table-driven FSM engine. Pure mechanical — no agent logic."""

import logging
from collections import deque
from typing import Callable, Optional

from agentnexus.agents.react_types import (
    ExecutionContext,
    ReActEvent,
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

    def _lookup(self, event: ReActEvent) -> Optional[Transition]:
        """Find the first matching transition for (current_state, event_type)."""
        for t in self._table:
            if t.state == self._state and (t.event is None or t.event == event.type):
                return t
        return None

    def run_loop(self, initial_event: ReActEvent, ctx: ExecutionContext,
                 handlers: dict) -> tuple[Optional[str], list]:
        """Process events until DONE state.

        Returns (last_answer, steps) — the same contract as ReActAgent.run().
        """
        self._state = ReActState.INIT
        self._queue.clear()
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
                continue

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
                        self._queue.append(ne)

            if t.next_state == ReActState.DONE:
                return (ctx.last_answer, ctx.steps)

        if self._state != ReActState.DONE:
            logger.warning("FSM exited in non-terminal state: %s", self._state)
            if not ctx.last_answer:
                ctx.last_answer = f"[Agent exited in state {self._state.name}]"

        return (ctx.last_answer, ctx.steps)

    def _try_auto_advance(self, ctx, handlers) -> bool:
        """If no events are queued, try an unconditional transition (event=None).

        Returns True if an unconditional transition was found and fired.
        """
        self._raise_if_cancelled(ctx)
        for t in self._table:
            if t.state == self._state and t.event is None:
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
                            self._queue.append(ne)

                if t.next_state == ReActState.DONE:
                    return True  # will be caught by outer loop's while check
                return True
        return False

    @staticmethod
    def _raise_if_cancelled(ctx) -> None:
        checker = getattr(ctx, "cancel_checker", None)
        if checker is not None and checker():
            raise RuntimeError("cancelled")
