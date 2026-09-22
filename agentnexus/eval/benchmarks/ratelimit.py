"""Token-bucket rate limiter with exponential-backoff retry for shared API pools.

Agnes free tier enforces ~20 RPM across *all* keys of an account type, and
GLM free models congest under load. Serializing calls through one limiter with
polite pacing beats parallel hammering: fewer 429s, predictable wall time.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

logger = logging.getLogger(__name__)


class RateLimiter:
    """Simple token bucket: ``rate_per_minute`` tokens, refilled continuously."""

    def __init__(self, rate_per_minute: float):
        if rate_per_minute <= 0:
            raise ValueError("rate_per_minute must be positive")
        self._interval = 60.0 / rate_per_minute
        self._lock = threading.Lock()
        self._next_slot = time.monotonic()

    def acquire(self) -> None:
        """Block until the next request slot is available."""
        with self._lock:
            now = time.monotonic()
            wait = max(0.0, self._next_slot - now)
            self._next_slot = max(now, self._next_slot) + self._interval
        if wait > 0:
            time.sleep(wait)


def call_with_retry(
    fn: Callable[[], str],
    limiter: RateLimiter,
    *,
    max_retries: int = 4,
    base_backoff_s: float = 5.0,
    empty_is_failure: bool = True,
) -> str:
    """Run ``fn`` through the limiter; on empty/exception, back off exponentially.

    Judge/generation calls that return empty text are treated as failures by
    default — the caller cannot distinguish "model refused" from a transient
    drop, and a missing score is worse than a retry.
    """
    attempt = 0
    while True:
        limiter.acquire()
        try:
            result = fn()
            if result or not empty_is_failure:
                return result or ""
        except Exception as exc:
            result = None
            logger.debug("eval call failed (attempt %d): %s", attempt + 1, exc)
        attempt += 1
        if attempt > max_retries:
            logger.warning("eval call exhausted %d retries, returning empty", max_retries)
            return ""
        backoff = base_backoff_s * (2 ** (attempt - 1))
        logger.debug("backing off %.1fs before retry %d", backoff, attempt + 1)
        time.sleep(backoff)
