"""Tests for agentnexus.server.rate_limit."""

import pytest
from fastapi import HTTPException

from agentnexus.server.rate_limit import RateLimiter


class TestRateLimiter:
    def test_default_rpm(self):
        limiter = RateLimiter()
        assert limiter.rpm == 60

    def test_custom_rpm(self):
        limiter = RateLimiter(requests_per_minute=30)
        assert limiter.rpm == 30

    def test_first_request_passes(self, mocker):
        mocker.patch("time.time", return_value=1000.0)
        limiter = RateLimiter(requests_per_minute=10)
        limiter.check("client-a")  # should not raise

    def test_requests_accumulate_within_window(self, mocker):
        mocker.patch("time.time", return_value=1000.0)
        limiter = RateLimiter(requests_per_minute=10)
        for _ in range(10):
            limiter.check("client-a")

    def test_exceeding_rpm_raises_429(self, mocker):
        mocker.patch("time.time", return_value=1000.0)
        limiter = RateLimiter(requests_per_minute=3)
        limiter.check("client-a")
        limiter.check("client-a")
        limiter.check("client-a")
        with pytest.raises(HTTPException) as exc_info:
            limiter.check("client-a")
        assert exc_info.value.status_code == 429
        assert exc_info.value.detail == "Rate limit exceeded"

    def test_after_60s_old_timestamps_pruned(self, mocker):
        mocker.patch("time.time", return_value=1000.0)
        limiter = RateLimiter(requests_per_minute=2)
        limiter.check("client-a")
        limiter.check("client-a")
        # Advance past the 60-second window
        mocker.patch("time.time", return_value=1061.0)
        limiter.check("client-a")  # should not raise

    def test_different_keys_tracked_independently(self, mocker):
        mocker.patch("time.time", return_value=1000.0)
        limiter = RateLimiter(requests_per_minute=1)
        limiter.check("client-a")
        limiter.check("client-b")  # different key, should pass
        with pytest.raises(HTTPException):
            limiter.check("client-a")

    def test_rpm_one_allows_exactly_one(self, mocker):
        mocker.patch("time.time", return_value=1000.0)
        limiter = RateLimiter(requests_per_minute=1)
        limiter.check("only-one")
        with pytest.raises(HTTPException):
            limiter.check("only-one")

    def test_new_key_empty_list_passes(self, mocker):
        mocker.patch("time.time", return_value=1000.0)
        limiter = RateLimiter(requests_per_minute=5)
        limiter.check("brand-new-key")

    def test_sliding_window_not_fixed(self, mocker):
        """Requests from earlier in the window are pruned as time moves forward."""
        mocker.patch("time.time", return_value=1000.0)
        limiter = RateLimiter(requests_per_minute=3)
        limiter.check("a")
        # One second later
        mocker.patch("time.time", return_value=1001.0)
        limiter.check("a")
        mocker.patch("time.time", return_value=1002.0)
        limiter.check("a")
        # Now at the limit; next should fail
        mocker.patch("time.time", return_value=1003.0)
        with pytest.raises(HTTPException):
            limiter.check("a")
        # Advance so the first request (t=1000) falls outside the window (1063 - 60 = 1003)
        mocker.patch("time.time", return_value=1061.0)
        limiter.check("a")  # oldest request pruned, now only 2 in window

    def test_exact_boundary_of_window(self, mocker):
        """Request exactly at window_start boundary is pruned (uses > not >=)."""
        mocker.patch("time.time", return_value=1000.0)
        limiter = RateLimiter(requests_per_minute=2)
        limiter.check("a")
        mocker.patch("time.time", return_value=1060.0)
        limiter.check("a")
        # At t=1060, window_start = 1060 - 60 = 1000.
        # The filter is `t > window_start`, so t=1000 is NOT > 1000, it gets pruned.
        # Only 1 request remains, so one more should pass.
        limiter.check("a")

    def test_rpm_zero_always_blocks(self, mocker):
        mocker.patch("time.time", return_value=1000.0)
        limiter = RateLimiter(requests_per_minute=0)
        with pytest.raises(HTTPException):
            limiter.check("a")
