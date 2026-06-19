"""Circuit breaker pattern for memory compaction and LLM gate.

Extracted from MemoryManager to isolate the state machine logic
from the memory lifecycle orchestration.
"""

from __future__ import annotations

import logging
import time
from enum import Enum

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    """Three-state circuit breaker."""
    CLOSED = "closed"       # normal operation
    OPEN = "open"           # tripped — reject calls
    HALF_OPEN = "half_open" # probe — allow one request to test recovery


class CircuitBreaker:
    """Generic circuit breaker with configurable thresholds and backoff.

    Usage::

        cb = CircuitBreaker(failure_threshold=3, recovery_seconds=20.0)
        if cb.should_allow():
            try:
                result = do_work()
                cb.record_success()
            except Exception:
                cb.record_failure()
        else:
            # in cooldown — skip or fallback
    """

    def __init__(
        self,
        failure_threshold: int = 3,
        recovery_seconds: float = 20.0,
        exponential_backoff: bool = False,
        backoff_base: float = 30.0,
        backoff_max: float = 120.0,
    ):
        self._failure_threshold = failure_threshold
        self._recovery_seconds = recovery_seconds
        self._exponential_backoff = exponential_backoff
        self._backoff_base = backoff_base
        self._backoff_max = backoff_max

        self._state = CircuitState.CLOSED
        self._failure_count: int = 0
        self._opened_at: float = 0.0

    @property
    def state(self) -> CircuitState:
        return self._state

    @property
    def failure_count(self) -> int:
        return self._failure_count

    @property
    def is_open(self) -> bool:
        return self._state is CircuitState.OPEN

    @property
    def is_half_open(self) -> bool:
        return self._state is CircuitState.HALF_OPEN

    @property
    def is_closed(self) -> bool:
        return self._state is CircuitState.CLOSED

    def _get_backoff_seconds(self) -> float:
        """Compute backoff duration. Exponential if enabled, else fixed."""
        if self._exponential_backoff:
            exponent = min(self._failure_count - self._failure_threshold, 2)
            return min(self._backoff_base * (2 ** exponent), self._backoff_max)
        return self._recovery_seconds

    def backoff_remaining(self) -> float:
        """Return seconds remaining in cooldown, or 0 if expired."""
        if self._state is not CircuitState.OPEN:
            return 0.0
        elapsed = time.time() - self._opened_at
        backoff = self._get_backoff_seconds()
        return max(0.0, backoff - elapsed)

    def should_allow(self) -> bool:
        """Check if a call should be allowed.

        Returns True if CLOSED, or if OPEN has expired (transitions to HALF_OPEN).
        """
        if self._state is CircuitState.CLOSED:
            return True
        if self._state is CircuitState.HALF_OPEN:
            return True
        # OPEN — check if cooldown expired
        if self.backoff_remaining() <= 0:
            self._state = CircuitState.HALF_OPEN
            return True
        return False

    def record_success(self) -> None:
        """Record a successful call. Resets to CLOSED."""
        self._state = CircuitState.CLOSED
        self._failure_count = 0

    def record_failure(self) -> None:
        """Record a failed call. May trip to OPEN."""
        if self._state is CircuitState.HALF_OPEN:
            # Half-open probe failed — back to OPEN
            self._state = CircuitState.OPEN
            self._opened_at = time.time()
            return
        self._failure_count += 1
        if self._failure_count >= self._failure_threshold:
            self._state = CircuitState.OPEN
            self._opened_at = time.time()

    def reset(self) -> None:
        """Force reset to CLOSED state."""
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._opened_at = 0.0
