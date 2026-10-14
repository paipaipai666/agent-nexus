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
    """Bind cancellation to the run context inherited by worker threads."""

    def __init__(self):
        self._checker: contextvars.ContextVar[Callable[[], bool] | None] = contextvars.ContextVar(
            "agentnexus_cancel_checker", default=None,
        )

    def set_checker(self, checker: Callable[[], bool] | None) -> None:
        self._checker.set(checker)

    def check(self) -> bool:
        """Return True when the parent run has been cancelled."""
        checker = self._checker.get()
        return bool(checker and checker())


class SubagentBridge:
    """Bind child execution to its parent run without cross-session sharing."""

    def __init__(self):
        self._ctx: contextvars.ContextVar = contextvars.ContextVar(
            "agentnexus_subagent_context", default=None,
        )

    def set_context(self, ctx) -> None:
        self._ctx.set(ctx)

    def get_context(self):
        return self._ctx.get()
