"""Structured metrics for the memory extraction pipeline.

Thread-safe counters for monitoring write volume, hit rates, conflict rates,
and gate health. Expose via report() for Prometheus or FastAPI /metrics.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field


@dataclass
class MemoryMetrics:
    """Thread-safe metrics collector for the memory subsystem."""

    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    # Write path
    writes_total: int = 0
    writes_skipped_dedup: int = 0
    writes_skipped_gate: int = 0
    writes_skipped_gate_error: int = 0          # LLM gate network/API exception
    writes_skipped_gate_format_error: int = 0   # LLM gate unexpected output format

    # PII
    pii_masked_count: int = 0                   # regex fallback caught PII that prompt missed

    # Conflict / supersede
    conflicts_detected: int = 0
    superseded_count: int = 0

    # Eviction / TTL
    deletions_expired: int = 0
    deletions_evicted: int = 0

    # Search
    searches_total: int = 0
    searches_hit: int = 0

    # Extraction lifecycle
    extraction_attempts: int = 0
    extraction_successes: int = 0

    # ── atomic increment ─────────────────────────────────────────────
    def incr(self, attr: str, n: int = 1) -> None:
        """Atomically increment a counter by *n*."""
        with self._lock:
            setattr(self, attr, getattr(self, attr) + n)

    # ── derived rates ────────────────────────────────────────────────
    @property
    def hit_rate(self) -> float:
        """Fraction of searches that returned at least one result."""
        return self.searches_hit / self.searches_total if self.searches_total else 0.0

    @property
    def conflict_rate(self) -> float:
        """Fraction of writes that involved a conflict detection."""
        return self.conflicts_detected / self.writes_total if self.writes_total else 0.0

    @property
    def gate_format_error_rate(self) -> float:
        """Fraction of gate calls that returned an unexpected format."""
        total_gate = (
            self.writes_skipped_gate
            + self.writes_skipped_gate_error
            + self.writes_skipped_gate_format_error
        )
        return self.writes_skipped_gate_format_error / total_gate if total_gate else 0.0

    # ── snapshot ─────────────────────────────────────────────────────
    def report(self) -> dict:
        """Return a plain dict snapshot of all counters and derived rates."""
        with self._lock:
            return {
                "writes_total": self.writes_total,
                "writes_skipped_dedup": self.writes_skipped_dedup,
                "writes_skipped_gate": self.writes_skipped_gate,
                "writes_skipped_gate_error": self.writes_skipped_gate_error,
                "writes_skipped_gate_format_error": self.writes_skipped_gate_format_error,
                "pii_masked_count": self.pii_masked_count,
                "conflicts_detected": self.conflicts_detected,
                "superseded_count": self.superseded_count,
                "deletions_expired": self.deletions_expired,
                "deletions_evicted": self.deletions_evicted,
                "searches_total": self.searches_total,
                "searches_hit": self.searches_hit,
                "hit_rate": round(self.hit_rate, 3),
                "conflict_rate": round(self.conflict_rate, 3),
                "gate_format_error_rate": round(self.gate_format_error_rate, 3),
                "extraction_attempts": self.extraction_attempts,
                "extraction_successes": self.extraction_successes,
            }


# ── module-level singleton ──────────────────────────────────────────
_metrics: MemoryMetrics | None = None
_metrics_lock = threading.Lock()


def get_metrics() -> MemoryMetrics:
    """Return the global MemoryMetrics singleton (created on first call)."""
    global _metrics
    if _metrics is None:
        with _metrics_lock:
            if _metrics is None:
                _metrics = MemoryMetrics()
    return _metrics
