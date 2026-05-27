"""Route telemetry for collecting training data and monitoring."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class RouteEvent:
    """A single routing event for telemetry and future training data."""
    timestamp: float = field(default_factory=time.time)
    query: str = ""
    query_terms: tuple[str, ...] = ()
    selected_skill: str | None = None
    selected_score: float = 0.0
    selected_source: str = ""
    candidates: tuple[dict[str, Any], ...] = ()
    mode: str = "single"  # single / multi_intent / ambiguous / abstain
    confidence: float = 0.0
    margin: float = 0.0
    accepted: bool | None = None  # None = not yet confirmed
    actual_skill: str | None = None  # what the user actually ended up using


class RouteTelemetry:
    """Collects routing events for analysis and future training."""

    def __init__(self, log_path: str | Path | None = None):
        self._log_path = Path(log_path) if log_path else None
        self._events: list[RouteEvent] = []

    def record(self, event: RouteEvent) -> None:
        self._events.append(event)
        if self._log_path:
            try:
                with open(self._log_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(asdict(event), ensure_ascii=False) + "\n")
            except OSError as exc:
                logger.debug("Failed to write route telemetry: %s", exc)

    def record_feedback(
        self, query: str, accepted: bool, actual_skill: str | None = None,
    ) -> None:
        """Record user feedback on a routing decision."""
        for event in reversed(self._events):
            if event.query == query and event.accepted is None:
                event.accepted = accepted
                event.actual_skill = actual_skill
                break

    @property
    def events(self) -> list[RouteEvent]:
        return list(self._events)

    def get_hard_negatives(self) -> list[dict[str, Any]]:
        """Extract cases where routing was close or wrong."""
        negatives = []
        for event in self._events:
            if event.mode == "ambiguous" or (event.accepted is False):
                negatives.append({
                    "query": event.query,
                    "selected": event.selected_skill,
                    "actual": event.actual_skill,
                    "candidates": event.candidates,
                    "margin": event.margin,
                })
        return negatives
