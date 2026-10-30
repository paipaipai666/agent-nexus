"""Engine contracts shared by application code and independent implementations."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class AgentSignal:
    """An engine event that does not depend on a state-machine implementation."""

    type: str
    payload: dict = field(default_factory=dict)
    step_id: int = 0


class AgentLoop(Protocol):
    """Run one task; interactive callbacks and metadata are optional capabilities."""

    def run(self, question: str, *, memory_manager=None, images=None, attachment_note=None):
        ...
