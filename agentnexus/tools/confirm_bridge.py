"""Mutable confirmation bridge for wiring TUI/parent approval into child tool closures."""

from __future__ import annotations

import contextvars
import threading
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class _ConfirmationTarget:
    callback: Callable[[str], bool]
    revoked: threading.Event = field(default_factory=threading.Event)
    lock: threading.RLock = field(default_factory=threading.RLock)

    def __call__(self, summary: str) -> bool:
        with self.lock:
            if self.revoked.is_set():
                return False
            approved = bool(self.callback(summary))
            return approved and not self.revoked.is_set()


class ConfirmBridge:
    def __init__(self):
        self._target: _ConfirmationTarget | None = None
        self._thread_targets: dict[int, _ConfirmationTarget] = {}
        self._context_target: contextvars.ContextVar[_ConfirmationTarget | None] = contextvars.ContextVar(
            "agentnexus_confirmation_target", default=None,
        )
        self._lock = threading.Lock()

    def set_target(self, target: Callable[[str], bool] | None, thread_id: int | None = None):
        replacement = _ConfirmationTarget(target) if target is not None else None
        with self._lock:
            if thread_id is None:
                previous = self._target
                self._target = replacement
            else:
                previous = self._thread_targets.pop(thread_id, None)
                if replacement is not None:
                    self._thread_targets[thread_id] = replacement
            if previous is not None:
                previous.revoked.set()
        if thread_id == threading.get_ident():
            self._context_target.set(replacement)

    def __call__(self, summary: str) -> bool:
        return self.confirm_as(threading.get_ident(), summary)

    def confirm_as(self, thread_id: int, summary: str) -> bool:
        """Use the inherited run target, falling back to the specified thread."""
        target = self._context_target.get()
        if target is None:
            with self._lock:
                target = self._thread_targets.get(thread_id, self._target)
        if target is None:
            return False
        return target(summary)


class CancelBridge:
    """Publish the parent agent's cancel checker to subagent tool closures.

    The checker is a plain Callable[[], bool] — safe to share across threads.
    Subagents cannot spawn their own subagents, so a single slot per registry
    suffices (no per-thread routing needed, unlike ConfirmBridge).
    """

    def __init__(self):
        self._checker: Callable[[], bool] | None = None

    def set_checker(self, checker: Callable[[], bool] | None) -> None:
        self._checker = checker

    def check(self) -> bool:
        """Return True when the parent run has been cancelled."""
        return bool(self._checker and self._checker())


class SubagentBridge:
    """Publish the active run's subagent context to subagent_run closures.

    Same single-slot tradeoff as CancelBridge: sessions sharing one registry
    share this slot — the last run to start wins (see the comment at
    re_act_agent.py where cancel_bridge.set_checker runs). The context exposes
    the run/session identity and the emit path the subagent tool forwards
    child-agent events through.
    """

    def __init__(self):
        self._ctx = None

    def set_context(self, ctx) -> None:
        self._ctx = ctx

    def get_context(self):
        return self._ctx
