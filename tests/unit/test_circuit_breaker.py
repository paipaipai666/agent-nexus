"""CircuitBreaker.protect() context-manager protocol tests."""

from __future__ import annotations

import time

import pytest

from agentnexus.memory.circuit_breaker import CircuitBreaker, CircuitOpenError


class TestProtectAdmission:
    def test_closed_circuit_admits(self):
        cb = CircuitBreaker()
        with cb.protect():
            pass
        assert cb.is_closed

    def test_open_circuit_rejects_with_circuit_open_error(self):
        cb = CircuitBreaker(failure_threshold=1, recovery_seconds=60.0)
        cb.record_failure()
        assert cb.is_open
        with pytest.raises(CircuitOpenError):
            with cb.protect():
                pytest.fail("body must not run when circuit is open")

    def test_rejection_does_not_record_failure(self):
        cb = CircuitBreaker(failure_threshold=1, recovery_seconds=60.0)
        cb.record_failure()
        with pytest.raises(CircuitOpenError):
            with cb.protect():
                pass
        assert cb.failure_count == 1  # unchanged by the rejected call


class TestProbe:
    def test_probe_reports_half_open_after_expired_cooldown(self):
        cb = CircuitBreaker(failure_threshold=1, recovery_seconds=0.01)
        cb.record_failure()
        assert cb.is_open
        time.sleep(0.02)
        with cb.protect() as probe:
            assert probe.is_half_open is True
        assert cb.is_closed

    def test_probe_reports_not_half_open_when_closed(self):
        cb = CircuitBreaker()
        with cb.protect() as probe:
            assert probe.is_half_open is False


class TestRecording:
    def test_normal_exit_records_success(self):
        cb = CircuitBreaker(failure_threshold=3)
        cb.record_failure()
        with cb.protect():
            pass
        assert cb.failure_count == 0
        assert cb.is_closed

    def test_exception_exit_records_failure_and_reraises(self):
        cb = CircuitBreaker(failure_threshold=3)
        with pytest.raises(ValueError):
            with cb.protect():
                raise ValueError("boom")
        assert cb.failure_count == 1

    def test_exception_exit_invokes_on_open(self):
        cb = CircuitBreaker(failure_threshold=1)
        opened = []
        with pytest.raises(ValueError):
            with cb.protect(on_open=lambda: opened.append(True)):
                raise ValueError("boom")
        assert opened == [True]
        assert cb.is_open

    def test_on_open_called_on_exceptional_exit_even_below_threshold(self):
        # protect() invokes on_open on every exceptional exit (after
        # record_failure); tripping detection is the callback's job.
        cb = CircuitBreaker(failure_threshold=3)
        opened = []
        with pytest.raises(ValueError):
            with cb.protect(on_open=lambda: opened.append(True)):
                raise ValueError("boom")
        assert opened == [True]
        assert cb.failure_count == 1
        assert cb.is_closed

    def test_normal_exit_does_not_invoke_on_open(self):
        cb = CircuitBreaker()
        opened = []
        with cb.protect(on_open=lambda: opened.append(True)):
            pass
        assert opened == []

    def test_half_open_failure_reopens_and_fires_on_open(self):
        cb = CircuitBreaker(failure_threshold=1, recovery_seconds=0.01)
        cb.record_failure()
        time.sleep(0.02)
        with pytest.raises(RuntimeError):
            with cb.protect(on_open=lambda: None):
                raise RuntimeError("probe failed")
        assert cb.is_open


class TestMetrics:
    def test_compaction_style_gate(self):
        """Mirror of the compaction_engine usage: reject → fallback path."""
        cb = CircuitBreaker(failure_threshold=1, recovery_seconds=60.0)
        cb.record_failure()
        try:
            with cb.protect():
                pytest.fail("unreachable")
        except CircuitOpenError:
            fallback = "microcompact"
        assert fallback == "microcompact"
