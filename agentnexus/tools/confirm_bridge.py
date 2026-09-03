"""Mutable confirmation bridge for wiring TUI/parent approval into child tool closures."""

from __future__ import annotations

import threading
from typing import Callable


class ConfirmBridge:
    def __init__(self):
        self._target: Callable[[str], bool] | None = None
        self._thread_targets: dict[int, Callable[[str], bool]] = {}

    def set_target(self, target: Callable[[str], bool] | None, thread_id: int | None = None):
        if thread_id is None:
            self._target = target
            return
        if target is None:
            self._thread_targets.pop(thread_id, None)
            return
        self._thread_targets[thread_id] = target

    def __call__(self, summary: str) -> bool:
        target = self._thread_targets.get(threading.get_ident(), self._target)
        if target is None:
            return False
        return bool(target(summary))


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
