"""Mutable confirmation bridge for wiring TUI/parent approval into child tool closures."""

from __future__ import annotations

from typing import Callable


class ConfirmBridge:
    def __init__(self):
        self._target: Callable[[str], bool] | None = None

    def set_target(self, target: Callable[[str], bool] | None):
        self._target = target

    def __call__(self, summary: str) -> bool:
        if self._target is None:
            return False
        return bool(self._target(summary))
